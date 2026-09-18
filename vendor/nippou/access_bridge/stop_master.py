"""全停入力・停止理由内訳マスタ（作業停止時間内訳_1/2/3）の参照。

VBAの ``boxin()``（~1905行）/ ``全停入力()``（~5273行）/
``LoadFieldArrayAndColumns``（~1986行）が、Access "伝送用ファイル.accdb"
の3テーブル（設備停止=作業停止時間内訳_1、不稼働=作業停止時間内訳_2、
ハンドリング=作業停止時間内訳_3、いずれも列は「内訳」「内訳番号」）
から読み込んでいた処理を移植したもの。参照専用（読み取りのみ）。

実データが未提供のため、想定テーブル構造（列名: 内訳/内訳番号）に基づく
暫定実装。実データ入手後、列名の突き合わせが必要な場合がある。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import SETTINGS
from ..logging_setup import get_logger
from .importer import import_table
from .runner import ScriptRunner

_logger = get_logger("access_bridge.stop_master")

# port of Table内訳1/2/3 (boxin()) -- カテゴリ名(表示用) -> テーブル名。
CATEGORY_TABLES: dict[str, str] = {
    "設備停止": "作業停止時間内訳_1",
    "不稼働": "作業停止時間内訳_2",
    "ハンドリング": "作業停止時間内訳_3",
}


@dataclass(frozen=True)
class StopReason:
    label: str  # 内訳
    code: str  # 内訳番号


def load_stop_reasons(
    category: str,
    master_path: Optional[Path] = None,
    runner: Optional[ScriptRunner] = None,
) -> list[StopReason]:
    """port of ``boxin()`` の内訳テーブル読み込み部分（1カテゴリ分）。"""
    table_name = CATEGORY_TABLES.get(category)
    if table_name is None:
        raise ValueError(f"未知のカテゴリです: {category}")

    master_path = master_path or SETTINGS.stop_reason_master_path
    result = import_table(master_path, table_name, runner=runner)
    if not result.success:
        _logger.warning("停止理由マスタの取得に失敗しました category=%s error=%s", category, result.error)
        return []

    reasons: list[StopReason] = []
    for row in result.rows:
        label = row.get("内訳", "").strip()
        if not label:
            continue
        reasons.append(StopReason(label=label, code=row.get("内訳番号", "").strip()))
    return reasons


def load_all_categories(
    master_path: Optional[Path] = None,
    runner: Optional[ScriptRunner] = None,
) -> dict[str, list[StopReason]]:
    """port of ``boxin()`` の3テーブル一括読み込み（ALLSTOP相当の統合元）。"""
    return {category: load_stop_reasons(category, master_path, runner) for category in CATEGORY_TABLES}


def find_reason_code(reasons: list[StopReason], label: str) -> str:
    """port of ``全停入力()`` の ``Answer`` 取得部分: 内訳名から内訳番号を検索する。"""
    for reason in reasons:
        if reason.label == label:
            return reason.code
    return ""
