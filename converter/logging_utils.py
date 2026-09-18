"""ログ出力(基盤仕様書 2.6)

【なぜ2本に分けるのか】
「起動しない」と言われたときに最初に知りたいのは**どこまで進んだか**
です。起動前に落ちたのか、Webサーバが上がらなかったのか、変換で失敗
したのか ── これが区別できないと、聞き取りから始めることになります。

    launcher.log  起動入口(環境の確認・多重起動の判定・待機画面・終了理由)
    app.log       アプリ本体(画面の操作・変換の開始と終了・エラー)

**細かく分けすぎない**のも仕様書の指示です。2本で上の区別はつきます。

置き場所は利用者ごとのローカル領域(`logs`)。アプリ本体は共有フォルダに
置かれうるので、そちらへ書くと他人のログと混ざります(2.7)。

【使い方】

    from converter.logging_utils import get_logger
    log = get_logger("jobs")          # → app.log
    log = get_logger("launcher")      # → launcher.log
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from . import app_config

#: 1本あたりの大きさと、残す世代。**共有へ大量生成しない**(2.7)ための上限
MAX_BYTES = 1024 * 1024
BACKUPS = 3

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

#: 起動入口のログへ入れる名前。**ここに挙がっていないものは app.log**
LAUNCHER_NAMES = frozenset({"launcher", "guard", "process_manager"})

#: 名前の前に付ける。他のアプリのロガーと混ざらないようにする
PREFIX = "xlcsv"

_console = False


def use_console(enabled: bool = True) -> None:
    """コンソールにも出す(診断起動 `start.bat` のとき)。

    **先に呼んでください。** すでに作ったロガーにも後から足します。
    """
    global _console
    _console = enabled
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(PREFIX + "."):
            _attach_console(logging.getLogger(name))


def log_file(name: str) -> Path:
    """その名前が書き込む先。**名前の作り方はここ1か所。**"""
    target = "launcher.log" if name in LAUNCHER_NAMES else "app.log"
    return app_config.local_dir("logs") / target


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """ログを1本取る。**同じ名前で2度呼んでも二重に出しません。**"""
    app_config.ensure_local_dirs()
    logger = logging.getLogger(f"{PREFIX}.{name}")
    logger.setLevel(level)
    logger.propagate = False
    path = log_file(name)
    if not any(getattr(h, "_xlcsv_path", None) == str(path)
               for h in logger.handlers):
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._xlcsv_path = str(path)           # type: ignore[attr-defined]
        logger.addHandler(handler)
    _attach_console(logger)
    return logger


def _attach_console(logger: logging.Logger) -> None:
    if not _console:
        return
    if any(type(h) is logging.StreamHandler for h in logger.handlers):
        return
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(stream)


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
    folder: Path = app_config.local_dir("logs")
    return {
        "folder": str(folder),
        "launcher": str(folder / "launcher.log"),
        "app": str(folder / "app.log"),
    }
