r"""アプリ固有値の唯一の出どころ(`config/app.json` の読み取り)

基盤仕様書 5.2 は、アプリごとに決める値(アプリケーションID・表示名・
使用ポート・ローカル保存フォルダー名・監視レベル)を**複数ファイルへ
書き散らさず1か所へ集める**ことを求めています。ここがその1か所です。

起動基盤(`start_app.py` / `launch_guard.py` / `server.py` /
`process_manager.py`)はこのモジュールだけを見ます。業務側の設定
(入力元・出力先・ライン・参照用マスタ)は利用者が画面で変えるものなので、
`user_settings.py` の担当です ── **配るときに決まる値**と**使う人が
決める値**を混ぜないための分け方です。

【ローカル領域】(基盤仕様書 2.7 / 4.6)

    %LOCALAPPDATA%\ExcelCsvConverter\
      runtime\ … PID・ポート・待機画面(停止後は消してよい)
      logs\    … launcher.log / guard.log / app.log
      pycache\ … 読込キャッシュ(アプリ本体が共有配置でも汚さない)
      cache\   … 再取得できるもの
      work\    … ドラッグで受け取ったブックなど一時のもの
      backup\  … 復旧目的で残すもの
      data\    … settings.json など**消してはいけない**もの
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

#: このファイルの1つ上 = リポジトリのルート(基盤仕様書のいう ApplicationRoot)
APP_ROOT = Path(__file__).resolve().parent.parent

#: 設定の置き場所。環境変数で差し替えられると、本番設定を書き換えずに試せる
CONFIG_PATH = Path(os.environ.get(
    "XLCSV_APP_CONFIG", str(APP_ROOT / "config" / "app.json")))

# `config/app.json` が読めないときの値。**設定が壊れていても起動はして、
# 画面に理由を出す**ほうが、起動そのものが失敗するより調べやすい
# (基盤仕様書 ステップ5)
_FALLBACK: dict[str, Any] = {
    "app_id": "nlm.excel-csv-converter",
    "display_name": "エクセル → CSV 変換器",
    "version": "0.0.0",
    "monitor_level": 2,
    "local_dir_name": "ExcelCsvConverter",
    "server": {"host": "127.0.0.1", "port": 8741},
    "limits": {"max_files": 100, "max_file_mb": 50},
    "monitoring": {
        "heartbeat_seconds": 3,
        "screen_timeout_seconds": 12,
        "job_poll_ms": 400,
        "idle_exit_minutes": 30,
    },
    "defaults": {"input_dir": "", "output_dir": "", "line": "", "ref_dir": "",
                 "stop_csv": "", "log_dir": ""},
    "lines": {"coil": ["LS"]},
}

_cache: Optional[dict[str, Any]] = None
_load_error: str = ""


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """既定値に設定の値をかぶせる。**書き忘れたキーは既定で埋まります。**"""
    result = _copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = _copy(value)
    return result


def load(*, force: bool = False) -> dict[str, Any]:
    """`config/app.json` を読む。壊れていても例外にせず既定値へ落とす。"""
    global _cache, _load_error
    if _cache is not None and not force:
        return _cache
    _load_error = ""
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("トップレベルがオブジェクトではありません")
        _cache = _merge(_FALLBACK, raw)
    except FileNotFoundError:
        _load_error = f"設定ファイルがありません: {CONFIG_PATH}"
        _cache = _copy(_FALLBACK)
    except Exception as exc:                      # noqa: BLE001 - 理由を残す
        _load_error = f"設定ファイルを読めませんでした({CONFIG_PATH}): {exc}"
        _cache = _copy(_FALLBACK)
    return _cache


def load_error() -> str:
    """既定値へ落ちた理由。正常なら空文字。"""
    load()
    return _load_error


# ------------------------------------------------------------------
# アプリの識別
# ------------------------------------------------------------------
def app_id() -> str:
    """起動確認APIで照合する識別子(基盤仕様書 2.3)。

    **同じポートを別のアプリが使っていることがあります。** HTTPが返って
    きたかどうかだけでは「うちが起動した」と言えないので、識別子を返して
    もらって突き合わせます。多重起動の判定もこれを使います。
    """
    return str(load()["app_id"])


def display_name() -> str:
    return str(load()["display_name"])


_VERSION_FORM = re.compile(r"^\d+\.\d+\.\d+$")


def version() -> str:
    return str(load()["version"])


def version_label() -> str:
    return f"VER{version()}"


def version_problem() -> str:
    """版の書き方がおかしければ理由。正しければ空文字(起動は止めません)。"""
    text = version()
    if _VERSION_FORM.match(text):
        return ""
    return (f"版の書き方が違います: {text!r}。config/app.json の version は"
            "「1.0.0」のように数字3つで書いてください。")


def monitor_level() -> int:
    """基盤仕様書 3章の監視レベル。

    本アプリは **2**(長時間処理アプリ)。100本の変換は分単位でかかる
    ことがあり、進捗と中止、ブラウザ再接続が要ります。自動実行は無いので
    3には上げません ── **必要以上に上げないこと**が仕様書の指示です。
    """
    return int(load()["monitor_level"])


# ------------------------------------------------------------------
# サーバ
# ------------------------------------------------------------------
def host() -> str:
    """`127.0.0.1` 固定。LANから届かず、ファイアウォール警告も出ません。"""
    return str(load()["server"]["host"])


def port() -> int:
    """待ち受けポート。**候補を持ちません。**

    ずらして逃げられるようにすると「2つ目が別のポートで起動できてしまう」
    ── このアプリが最も避けたいことです(混乱のもと)。ポートが1つだけ
    なら、束縛できないこと自体が多重起動の歯止めになります。
    使えないポートなら `config/app.json` で変えてください。
    """
    return int(load()["server"]["port"])


def base_url() -> str:
    return f"http://{host()}:{port()}/"


# ------------------------------------------------------------------
# 受け入れの上限
# ------------------------------------------------------------------
def max_files() -> int:
    """一度に受ける本数の上限(既定100)。"""
    return int(load()["limits"]["max_files"])


def max_file_bytes() -> int:
    return int(load()["limits"]["max_file_mb"]) * 1024 * 1024


# ------------------------------------------------------------------
# ライン ── コイルと板で、機側データ集計の列が変わります
# ------------------------------------------------------------------
def coil_lines() -> tuple[str, ...]:
    r"""コイルのライン名(既定 `LS`)。**ここに無いラインは板あつかい**です。

    機側データ集計の後ろ3列が入れ替わります(コイルは縦割・横割・
    係数処理ﾛｯﾄ数、板は合紙・ＶＣ種別)。コイルのラインが増えたら
    `config/app.json` の `lines.coil` に足してください ── 現場で変わる
    値なので、コードではなく設定に置いてあります。
    """
    found = load().get("lines", {}).get("coil", [])
    if isinstance(found, str):
        found = [found]
    return tuple(str(one).strip() for one in found if str(one).strip())


# ------------------------------------------------------------------
# 監視(基盤仕様書 2.9)
# ------------------------------------------------------------------
def heartbeat_seconds() -> int:
    """画面がバックエンドへ生存確認を送る間隔。"""
    return int(load()["monitoring"]["heartbeat_seconds"])


def screen_timeout_seconds() -> int:
    """この秒数だけ生存確認が途切れたら、画面は居なくなったとみなします。

    短すぎると再読込のたびに空きますし、長すぎると閉じたあと開き直せない
    時間が伸びます。既定は生存確認の4回ぶん。
    """
    return int(load()["monitoring"]["screen_timeout_seconds"])


def job_poll_ms() -> int:
    return int(load()["monitoring"]["job_poll_ms"])


def idle_exit_minutes() -> int:
    """画面が無いまま何分でバックエンドを終わらせるか(0で無効)。

    **変換中は終わりません。** 閉じた画面を待ち続けて常駐すると、次の
    起動が「すでに起動しています」で断られ続けることになります。
    """
    return int(load()["monitoring"]["idle_exit_minutes"])


def defaults() -> dict[str, str]:
    """業務設定の初期値(配るときに決める値)。"""
    return {k: str(v) for k, v in load()["defaults"].items()}


# ------------------------------------------------------------------
# ユーザー別ローカル領域(基盤仕様書 2.7 / 4.6)
# ------------------------------------------------------------------
def local_root() -> Path:
    """`%LOCALAPPDATA%\\<local_dir_name>`(非Windowsは XDG 相当)。

    `XLCSV_LOCAL_DIR` で丸ごと差し替えられます ── 検証とテストが本番の
    領域を汚さずに済むようにするための逃げ道です。
    """
    override = os.environ.get("XLCSV_LOCAL_DIR")
    if override and override.strip():
        return Path(override.strip())
    name = str(load()["local_dir_name"])
    base = os.environ.get("LOCALAPPDATA")
    if base:                                      # Windows
        return Path(base) / name
    xdg = os.environ.get("XDG_DATA_HOME")         # 開発機・CI
    if xdg:
        return Path(xdg) / name
    return Path.home() / ".local" / "share" / name


LOCAL_SUBDIRS = ("runtime", "logs", "pycache", "cache", "work", "backup", "data")


def local_dir(name: str) -> Path:
    if name not in LOCAL_SUBDIRS:
        raise ValueError(
            f"未知のローカル領域: {name!r}(使えるのは {', '.join(LOCAL_SUBDIRS)})")
    return local_root() / name


def ensure_local_dirs() -> Path:
    root = local_root()
    for name in LOCAL_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def instance_path() -> Path:
    """起動中の目印(PID・ポート・URL)。停止すれば消します。"""
    return local_dir("runtime") / "instance.json"


def describe() -> str:
    """診断用の1枚。`start.bat` とログの先頭に出します(基盤仕様書 2.6)。"""
    lines = [
        f"アプリID      : {app_id()}",
        f"表示名        : {display_name()}",
        f"バージョン    : {version()}",
        f"監視レベル    : {monitor_level()}",
        f"設定ファイル  : {CONFIG_PATH}",
        f"アプリ本体    : {APP_ROOT}",
        f"ローカル領域  : {local_root()}",
        f"待ち受け      : {base_url()}",
        f"受け入れ上限  : {max_files()} 本 / 1本 {max_file_bytes() // 1024 // 1024}MB",
    ]
    if load_error():
        lines.append(f"【注意】{load_error()} — 既定値で動作しています")
    if version_problem():
        lines.append(f"【注意】{version_problem()}")
    return "\n".join(lines)


if __name__ == "__main__":        # python -m converter.app_config で確かめられます
    print(describe())
