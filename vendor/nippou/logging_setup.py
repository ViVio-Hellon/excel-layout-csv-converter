"""Logging setup replacing the VBA ``DebugLog`` / ``WriteLog`` helpers.

The original tool wrote timestamped, comma-separated lines to a text file
next to the workbook (``InitializeDebugLog`` / ``DebugLog``) and had a
separate ``UserLog`` sink that mirrored messages into a textbox on the
form. Here we use the standard :mod:`logging` module: a rotating file
handler covers the "troubleshooting log file" requirement (execution
history, button clicks, target line, etc.) and callers can attach any
number of extra handlers (e.g. a Tk widget) the same way ``SetUserLogBox``
used to.

The handler is attached to the *root* logger rather than just the
``nippou`` logger tree. ``dbkit`` (the shared DB layer used across
several VBA migration projects, see ``dbkit.logging_utils.get_logger``)
logs under its own ``dbkit.*`` namespace and relies on normal ``logging``
propagation to reach whatever handler the host application installed --
attaching only to ``nippou`` would silently drop every dbkit log line
instead of landing it in ``nippou.log`` alongside everything else.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import SETTINGS

LOGGER_NAME = "nippou"


def init_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure and return the application logger.

    Safe to call more than once (e.g. once at startup and once when the
    admin panel wants to redirect output) -- existing handlers of the
    same kind are not duplicated.
    """
    log_dir = log_dir or SETTINGS.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "nippou.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    has_file_handler = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "_nippou_log_path", None) == log_path
        for h in root_logger.handlers
    )
    if not has_file_handler:
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler._nippou_log_path = log_path  # dedupe marker for repeated init_logging() calls
        handler.setFormatter(
            logging.Formatter("%(asctime)s\t%(levelname)s\t%(name)s\t%(message)s")
        )
        root_logger.addHandler(handler)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    return logger


def get_logger(component: str | None = None) -> logging.Logger:
    name = LOGGER_NAME if component is None else f"{LOGGER_NAME}.{component}"
    return logging.getLogger(name)


# ------------------------------------------------------------------
# 起動フェーズのログ (基盤仕様書 2.6)
# ------------------------------------------------------------------
# 仕様書は「起動前に失敗したのか」「Webサーバが失敗したのか」「業務処理で
# 失敗したのか」を**区別できる**ことを求めている。業務ログ(nippou.log)と
# 同じファイルに混ぜると、起動できなかったときに読む場所が分からない
# ので、起動基盤は自分用のファイルへ書く。
#
#   launcher.log … start_app(環境確認・ブラウザ起動)
#   guard.log    … launch_guard(多重起動判定)/ server / process_manager
_LAUNCH_LOGGER_PREFIX = "nippou.launch"


def get_launch_logger(component: str, *,
                      filename: str = "launcher.log",
                      level: int = logging.INFO) -> logging.Logger:
    """起動基盤用のロガー。``logs/<filename>`` へ書く。

    **業務ログとは別のファイルにする。** アプリ本体が組み上がる前の
    出来事なので、`init_logging()` がまだ呼ばれていないことがある
    (呼ばれていても、混ぜると読む場所が分からなくなる)。

    コンソールにも出す ── `start.bat`(診断起動)から実行したときに、
    利用者がその場で理由を読めるようにするため。`pythonw.exe` では
    `sys.stderr` が `None` になるので、書ける場合だけ付ける。
    """
    logger = logging.getLogger(f"{_LAUNCH_LOGGER_PREFIX}.{component}")
    logger.setLevel(level)
    # 業務ログのハンドラ(rootに付く)へ流さない。起動の記録が
    # nippou.log にも二重に出ると、どちらを読めばよいか分からなくなる
    logger.propagate = False

    log_path = _launch_log_path(filename)
    already = any(getattr(h, "_nippou_log_path", None) == log_path
                  for h in logger.handlers)
    if not already and log_path is not None:
        try:
            handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        except OSError:
            # ログを書けないことを理由に起動を止めない
            handler = None
        if handler is not None:
            handler._nippou_log_path = log_path
            handler.setFormatter(logging.Formatter(
                "%(asctime)s\t%(levelname)s\t%(name)s\t%(message)s"))
            logger.addHandler(handler)

    if _has_console() and not any(_is_console_handler(h) for h in logger.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                               datefmt="%H:%M:%S"))
        logger.addHandler(console)
    return logger


def _launch_log_path(filename: str) -> Path | None:
    try:
        log_dir = SETTINGS.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / filename
    except OSError:
        return None


def _has_console() -> bool:
    """標準エラー出力に書けるか。

    `pythonw.exe`(コンソールを出さない起動)では `sys.stderr` が `None`
    になる。`StreamHandler()` はそのとき `stream=None` を抱え、1行書く
    たびに例外を起こす(`logging` が握りつぶすので表には出ない)。
    """
    import sys
    return getattr(sys, "stderr", None) is not None and hasattr(sys.stderr, "write")


def _is_console_handler(handler: logging.Handler) -> bool:
    # FileHandler は StreamHandler の派生なので先に除外する
    return (isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler))


def log_button_click(button_name: str, line: str = "", extra: str = "") -> None:
    """Convenience helper: records a UI button click, mirroring the dense
    ``Call DebugLog("【btnXxx_Click】...")`` trail left throughout the VBA
    form module."""
    get_logger("ui").info("button_click name=%s line=%s %s", button_name, line, extra)
