"""ログの初期化(基盤仕様書 2.6)

【なぜ分けるのか】
「起動しない」と言われたときに知りたいのは、まず**どこまで進んだか**
です。起動前に落ちたのか、Webサーバが上がらなかったのか、変換で失敗した
のか ── これが区別できないと、聞き取りから始めることになります。

    launcher.log  起動入口(環境の確認・多重起動の判定・待機画面・終了理由)
    app.log       アプリ本体(画面の操作・変換の開始と終了・エラー)

**細かく分けすぎない**のも仕様書の指示です。2本で上の区別はつきます。

置き場所は利用者ごとのローカル領域(`logs`)です。アプリ本体は共有
フォルダに置かれうるので、そちらへ書くと他人のログと混ざります(2.7)。
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from . import appinfo

#: 1本あたりの大きさと、残す世代。**共有へ大量生成しない**(2.7)ための上限
MAX_BYTES = 1024 * 1024
BACKUPS = 3

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(name: str, filename: str, *, console: bool = False,
          level: int = logging.INFO) -> logging.Logger:
    """ログを1本用意する。**同じ名前で2度呼んでも二重に出しません。**"""
    appinfo.ensure_local_dirs()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    path = appinfo.local_dir("logs") / filename
    if not any(getattr(h, "_xlcsv_path", None) == str(path)
               for h in logger.handlers):
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._xlcsv_path = str(path)           # type: ignore[attr-defined]
        logger.addHandler(handler)
    if console and not any(isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)
                           for h in logger.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(stream)
    return logger


def launcher(*, console: bool = False) -> logging.Logger:
    """起動入口のログ。`start_app.py` と `process_manager.py` が使います。"""
    return setup("xlcsv.launcher", "launcher.log", console=console)


def application(*, console: bool = False) -> logging.Logger:
    """アプリ本体のログ。画面とサーバと変換が使います。"""
    return setup("xlcsv.app", "app.log", console=console)


def quiet_vendor(verbose: bool = False) -> None:
    """写してある日報管理ツール側のログを黙らせる。

    参照用マスタが読めないときの警告は**ファイルの数だけ**出ます
    (1本につき3回)。100本なら300行。知らせる値打ちがあるのは1回目だけ
    なので、画面には `jobs.master_notice()` が1行で出し、ここは黙らせます。
    """
    logging.getLogger("nippou").setLevel(
        logging.DEBUG if verbose else logging.CRITICAL)


def log_paths() -> dict[str, str]:
    """画面とエラー表示に出す「どこを見ればよいか」。"""
    folder: Path = appinfo.local_dir("logs")
    return {
        "folder": str(folder),
        "launcher": str(folder / "launcher.log"),
        "app": str(folder / "app.log"),
    }
