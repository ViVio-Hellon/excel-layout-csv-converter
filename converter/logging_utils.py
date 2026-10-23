"""ログ出力(基盤仕様書 2.6)

【なぜ3本に分けるのか】
「起動しない」「出ない」と言われたときに最初に知りたいのは**どこまで
進んだか**です。起動前に落ちたのか、Webサーバが上がらなかったのか、
変換で失敗したのか ── これが区別できないと、聞き取りから始めることに
なります。

    <ツール名>_launcher.log  起動入口(環境の確認・多重起動の判定・終了理由)
    <ツール名>_app.log       アプリ本体(画面の操作・変換の筋道・1本ごとの結果)
    <ツール名>_error.log     **うまくいかなかったことだけ**(警告以上。中身つき)

**細かく分けすぎない**のも仕様書の指示です。3本目は「分類」ではなく
**入口**です ── あとから追うとき、まず `_error.log` を開いて時刻と仕事の
番号(`[job=ab12cd34]`)を拾い、その番号で `_app.log` を追えば、同じ仕事の
筋道が頭から終わりまで並んでいます。

【なぜ名前とフォルダの両方にツール名を入れるのか】
共有の掃き出し先には**いろいろなツールのログが集まります**。

    \\共有\ログ\ExcelCsvConverter\ExcelCsvConverter_app.log
                  dbviewer\…
                  rneconverter\…

フォルダだけで分けると、**1本取り出した時点でどのツールのものか分から
なくなります**(開くまで分からない)。名前だけに入れると、同じ場所に
何十本も並びます。だから両方に入れます ── 現場の言葉で
「取り出したとき結局開かないとわからなくなるため」。

【なぜなぜ分析ができる書き方】
1行ずつ「いつ・どの仕事の・何本目の・どのファイルで・何をして・どうだった」
が入ります。**理由は画面に出すのと同じ言葉**で残します(画面を閉じたあとで
追えるように)。例外は種類とメッセージだけでなく**中身(traceback)**も
`error.log` に残します ── 「読めませんでした」だけでは、なぜ読めなかったか
に進めません。

    [job=ab12cd34] 変換開始 版=1.8.1 本数=7 ライン=(自動) 出力先=… 参照用マスタ=…
    [job=ab12cd34] 1/7 2025.07.16.xlsx 開きました 大きさ=49,793バイト
    [job=ab12cd34] 1/7 2025.07.16.xlsx シート=2025年7月15日_機側 作業日=2025年7月15日 …
    [job=ab12cd34] 1/7 2025.07.16.xlsx 読めました ページ=4 行=27 検算=8項目すべて一致…

【置き場所】
既定は利用者ごとのローカル領域(`logs`)です。アプリ本体は共有フォルダに
置かれうるので、そちらへ書くと他人のログと混ざります(2.7)。

**設定部の「ログの置き場所」で変えられます**(共有へ集めて見たいとき)。
書けない場所を指されたらローカルへ戻し、**戻した理由を残します** ──
ログが黙って消えるのがいちばん困るので。

【使い方】

    from converter.logging_utils import get_logger
    log = get_logger("jobs")          # → <ツール名>_app.log
    log = get_logger("launcher")      # → <ツール名>_launcher.log
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from . import app_config

#: 1本あたりの大きさと、残す世代。**共有へ大量生成しない**(2.7)ための上限
#: (1本ごとの筋道まで残すので、1MBだと1〜2日で消えます)
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 5

#: ログの種類。**ファイル名はこれにツール名を足して作ります**
APP_LOG = "app"
ERROR_LOG = "error"            # うまくいかなかったことだけ。**追うときの入口**
LAUNCHER_LOG = "launcher"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

#: 起動入口のログへ入れる名前。**ここに挙がっていないものは _app.log**
LAUNCHER_NAMES = frozenset({"launcher", "guard", "process_manager"})

#: 名前の前に付ける。他のアプリのロガーと混ざらないようにする
PREFIX = "xlcsv"

_console = False

#: いま書いている場所(`None` ならローカル領域)。`use_folder` が決めます
_folder: Optional[Path] = None

#: ローカルへ戻した理由(空なら戻していない)。画面と診断に出します
_note = ""


def folder() -> Path:
    """いまログを書いている場所。**ツール名のフォルダまで**を返します。

    指した先が共有なら `<指した先>\<ツール名>\` です(`use_folder` が
    作ります)。空のときは端末のローカル領域
    `%LOCALAPPDATA%\<ツール名>\logs` ── こちらは**上の階層が既に
    ツール名**なので、重ねません。
    """
    return _folder or app_config.local_dir("logs")


def tool_name() -> str:
    """ログに入れるツール名(`config/app.json` の `log_name`)。"""
    return app_config.log_name()


def file_name(kind: str) -> str:
    """ログのファイル名。**名前の作り方はここ1か所。**

    `ExcelCsvConverter_app.log` のように、**必ずツール名から始めます** ──
    共有に集めたログを1本取り出したとき、開かなくても分かるように。
    """
    return f"{tool_name()}_{kind}.log"


def note() -> str:
    """指した場所へ書けなかったときの断り(空なら何も起きていない)。"""
    return _note


def use_folder(text: str) -> str:
    r"""ログの置き場所を変える。**戻り値は断りの言葉**(空なら成功)。

    書けるかどうかを**その場で試します**(試し書きして消す)── 書けない
    場所を黙って受けると、以後のログがどこにも残りません。共有が落ちて
    いた・権限が無い、といったときはローカル領域へ戻し、その理由を
    `error.log` と画面(設定部・診断情報)に残します。
    """
    global _folder, _note

    want = (text or "").strip().strip('"').strip()
    target: Optional[Path] = None
    if want:
        try:
            path = Path(want).expanduser()
            if not path.is_absolute():
                path = app_config.APP_ROOT / path
            # **ツール名のフォルダを1つ作って、その中へ出します** ──
            # 共有の掃き出し先は何本ものツールで共用されます。
            # 指した先が既にツール名なら重ねません
            if path.name != tool_name():
                path = path / tool_name()
            path.mkdir(parents=True, exist_ok=True)
            # 試すのは**実際に書く場所**です(親だけ試すと、作った
            # フォルダに書けないときに気づけません)
            probe = path / ".write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            target = path
        except Exception as exc:                  # noqa: BLE001 - 既定へ戻す
            _folder, _note = None, (
                f"ログの置き場所へ書けないので、ローカル領域に出しています"
                f"({want}: {exc})")
            _rebind()
            get_logger("logging").warning(_note)
            return _note
    _folder, _note = target, ""
    _rebind()
    return ""


def _rebind() -> None:
    """すでに作ったログを、いまの場所へ向け直す。"""
    for name in list(logging.root.manager.loggerDict):
        if not name.startswith(PREFIX + "."):
            continue
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if getattr(handler, "_xlcsv_path", None):
                logger.removeHandler(handler)
                handler.close()
        _attach_files(logger, name[len(PREFIX) + 1:])


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
    """その名前が書き込む先。"""
    kind = LAUNCHER_LOG if name in LAUNCHER_NAMES else APP_LOG
    return folder() / file_name(kind)


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """ログを1本取る。**同じ名前で2度呼んでも二重に出しません。**"""
    app_config.ensure_local_dirs()
    logger = logging.getLogger(f"{PREFIX}.{name}")
    logger.setLevel(level)
    logger.propagate = False
    _attach_files(logger, name)
    _attach_console(logger)
    return logger


def _attach_files(logger: logging.Logger, name: str) -> None:
    """その名前のログ(と `_error.log`)を結ぶ。**二重には結びません。**"""
    for path, least in ((log_file(name), logging.NOTSET),
                        (folder() / file_name(ERROR_LOG), logging.WARNING)):
        if any(getattr(h, "_xlcsv_path", None) == str(path)
               for h in logger.handlers):
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        except OSError:                           # 書けない場所(次の試しで戻す)
            continue
        handler.setFormatter(logging.Formatter(_FORMAT))
        if least:
            handler.setLevel(least)
        handler._xlcsv_path = str(path)           # type: ignore[attr-defined]
        logger.addHandler(handler)


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
    where = folder()
    return {
        "folder": str(where),
        "tool": tool_name(),
        "launcher": str(where / file_name(LAUNCHER_LOG)),
        "app": str(where / file_name(APP_LOG)),
        "error": str(where / file_name(ERROR_LOG)),
        "note": _note,
        "default": str(app_config.local_dir("logs")),
    }
