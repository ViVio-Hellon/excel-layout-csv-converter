r"""テストが本物の記録を書き換えないようにする

【なぜ `tests/__init__.py` だけでは足りないのか】
`tests/__init__.py` は環境変数を先に立てて、ローカル領域(ログ・設定・
受け取ったブック)を一時フォルダへ向けています。これは**その
`__init__.py` が読まれる場合に限って**効きます。ところが unittest の
探索は、開始フォルダをそのまま最上位として扱うと、テストを**パッケージ
配下ではなく単体のモジュールとして**取り込みます。

    python3 -m unittest discover -s tests        # tests/__init__.py は読まれない
    python3 -m unittest discover -s tests -t .   # 読まれる(READMEはこちら)

前者で流すと安全網が外れ、`%LOCALAPPDATA%\ExcelCsvConverter` の
`settings.json` とログが**本番の記録と混ざります**。黙って起きるのが
厄介で、テストは全部OKのまま端末の設定だけが書き換わります。

そこでこのモジュールが、**いま向いている場所を見て**危なければ一時
フォルダへ向け直します。取り込み時に直せば間に合うのは、unittest が
テストを全部取り込んでから走らせるからです(`test_isolation.py` が
呼んでいます)。
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

#: 何度呼ばれても1回だけ効かせる(取り込み順に依存させない)
_done = False

ENV = "XLCSV_LOCAL_DIR"

#: 配布設定の置き場所(`配布設定\`)。**本物のツールのフォルダに作らない**
#: ために、ここも一時フォルダへ逃がします
ENV_LAUNCH = "XLCSV_LAUNCH_DIR"


def _escape(name: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix=f"xlcsv_test_{name}_"))
    atexit.register(shutil.rmtree, tmp, True)
    return tmp


def _is_temp(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except (ValueError, OSError):
        return False
    return True


def ensure_isolated() -> list[str]:
    """危ない向き先を一時フォルダへ替える。替えた項目の名前を返す。"""
    global _done
    if _done:
        return []
    _done = True

    _quiet_vendor()
    escaped: list[str] = []
    for name, label in ((ENV, "local"), (ENV_LAUNCH, "launch")):
        current = os.environ.get(name, "")
        if current and _is_temp(Path(current)):
            continue                               # すでに逃がしてある
        os.environ[name] = str(_escape(label))
        escaped.append(name)
    return escaped


def _quiet_vendor() -> None:
    """写してある日報管理ツールの警告を黙らせる。

    参照用マスタはテストに要りません。読めない警告がファイルの数だけ出て、
    **本当に見たい失敗が埋もれます**(本番は画面が1行だけ出します)。
    """
    import logging
    logging.getLogger("nippou").setLevel(logging.CRITICAL)
    logging.getLogger("dbkit").setLevel(logging.CRITICAL)
