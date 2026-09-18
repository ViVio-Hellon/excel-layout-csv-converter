"""月が替わったかを決める (VBA ``月替わりチェック実行``)

【VBA は何を見ていたか】
ブックの中には日付の付いたシートが溜まっていきます。その中から
**集計用のシート(A1が「集計」)だけ**を拾って日付を読み、

    ・2つ以上の月が混ざっている        → 月替わり。**古いほう**を片付ける
    ・いちばん新しい月 ≠ 今月           → 月替わり。その月を片付ける
    ・いちばん新しい月 = 今月           → まだ月の途中

と決めていました。「今日が1日だから」ではなく、**溜まっているデータの
月**で決めるのが要点です ── 月初の何日かは前月の3直を打つので、
日付だけで切ると、まだ書いている月を片付けてしまいます。

【このツールでの見方】
シートの代わりに、手元のDBに入っている報告日の (年, 月) を見ます
(`repository.month_keys`)。判断の中身は VBA のままです。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class RollDecision:
    """月替わりかどうかと、片付ける月。**理由も持つ。**

    理由まで返すのは、「なぜいま出てきたのか」が分からないと、
    月に1度しか起きない動きを人が確かめられないからです。
    """

    due: bool = False
    year: int = 0
    month: int = 0
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.year}年{self.month}月" if self.year else ""

    def as_dict(self) -> dict:
        return {"due": self.due, "year": self.year, "month": self.month,
                "label": self.label, "reason": self.reason}


def decide(months: Iterable[tuple[int, int]], today: date) -> RollDecision:
    """port of ``月替わりチェック実行``。

    `months` は入っている報告日の (年, 月)。順不同でも構いません。
    """
    found: Sequence[tuple[int, int]] = sorted(set(months))
    if not found:
        return RollDecision(reason="データがありません")

    oldest, newest = found[0], found[-1]
    now = (today.year, today.month)

    if len(found) > 1:
        return RollDecision(
            True, *oldest,
            reason=f"{len(found)}か月ぶんが混ざっています。"
                   f"古いほうの{oldest[0]}年{oldest[1]}月を片付けます")
    if newest != now:
        return RollDecision(
            True, *newest,
            reason=f"入っているのは{newest[0]}年{newest[1]}月ぶんだけで、"
                   f"今は{now[0]}年{now[1]}月です")
    return RollDecision(
        False, *newest,
        reason=f"{newest[0]}年{newest[1]}月の途中です")


def month_folder_name(year: int, month: int) -> str:
    """月別フォルダの名前。VBA ``Format(processDate, "yyyy.mm")`` と同じ。"""
    return f"{year:04d}.{month:02d}"


def month_file_stem(year: int, month: int, line: str) -> str:
    """書き出すファイル名の頭。VBA ``yyyy年mm月_<ライン>.xlsx`` と同じ形。"""
    return f"{year:04d}年{month:02d}月_{safe_name(line)}"


_UNSAFE = '\\/:*?"<>|'


def safe_name(text: str) -> str:
    """フォルダ名・ファイル名に使える形にする。空なら ``Unknown``。"""
    cleaned = "".join(ch for ch in (text or "") if ch not in _UNSAFE).strip()
    return cleaned or "Unknown"


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def parse_month(text: str) -> Optional[tuple[int, int]]:
    """``"2026-08"`` / ``"2026.08"`` / ``"2026年8月"`` を (年, 月) に。"""
    raw = (text or "").strip().replace("年", "-").replace("月", "").replace(".", "-")
    parts = [p for p in raw.split("-") if p]
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12) or year < 1900:
        return None
    return year, month
