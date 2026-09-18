r"""起動の開始点(基盤仕様書 2.1 / 2.2 / 2.4 / 2.5)

アプリ本体を読む前に、実行環境を整えて、起動してよいかを決めます。

    1. 実行環境を確認中   Python の版・アプリの置き場所・設定・ローカル領域
    2. アプリを準備中     多重起動の判定 → 待機画面 → 待ち受け開始 → 読込
    3. 接続を確認中       待機画面が起動確認APIの応答を見る
    4. 起動完了           画面へ切り替わる

【なぜ「すでに起動しています」で断るのか】
2つ目を起動させると、同じ設定を2つのプロセスが書き、どちらの画面の操作が
効いているのか分からなくなります。**開くこと自体をお断りします** ──
既存の画面が見当たらないときだけ、聞いたうえで開き直します。

    通常起動  Start.vbs → pythonw start_app.py     (コンソールを出さない)
    診断起動  start.bat → python start_app.py --diagnose
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 写してある日報管理ツール。**同じコードを通すため**にここに置いてあります
sys.path.insert(0, str(HERE / "vendor"))

#: 動かすのに要る Python。標準ライブラリだけで書いてありますが、
#: `X | None` の書き方(3.10) と `tomllib` 以降の作法に合わせて 3.11 以上
MIN_PYTHON = (3, 11)

EXIT_OK = 0
EXIT_ENV = 1            # 実行環境が足りない
EXIT_ALREADY = 3        # すでに起動している(断った)
EXIT_PORT = 4           # ポートが塞がっている


# ------------------------------------------------------------------
# 利用者への知らせ ── コンソールが無くても届くように
# ------------------------------------------------------------------
def tell(text: str, *, title: str = "", ask: bool = False) -> bool:
    """画面に出す。**Start.vbs からはコンソールがありません。**

    Windows では MessageBox を出します(`ctypes` だけで出せます)。
    `ask` なら「はい/いいえ」で聞き、押された側を返します。
    """
    print(text)
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        flags = 0x00000004 | 0x00000020 if ask else 0x00000040   # YESNO / OK
        answer = ctypes.windll.user32.MessageBoxW(
            None, text, title or "エクセル → CSV 変換器", flags | 0x00001000)
        return answer == 6                        # IDYES
    except Exception:                             # noqa: BLE001
        return False


# ------------------------------------------------------------------
# 1. 実行環境を確認中
# ------------------------------------------------------------------
def check_environment() -> list[str]:
    """足りないものを並べる。**空なら進めます。**"""
    found: list[str] = []
    if sys.version_info < MIN_PYTHON:
        found.append(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が要ります"
            f"(いまは {sys.version.split()[0]}: {sys.executable})")
    if not (HERE / "vendor" / "nippou").is_dir():
        found.append(f"vendor が見当たりません({HERE / 'vendor'})。"
                     "フォルダごとコピーできているか確かめてください")
    if not (HERE / "converter").is_dir():
        found.append(f"converter が見当たりません({HERE / 'converter'})")
    return found


def prepare_local() -> None:
    r"""ローカル領域を用意し、読込キャッシュをそちらへ向ける(基盤仕様書 2.7)。

    アプリ本体は共有フォルダに置かれることがあります。`__pycache__` を
    そこへ作ると、同期の競合と権限の問題を呼びます。**書くものは
    `%LOCALAPPDATA%` へ。**
    """
    from app import appinfo
    appinfo.ensure_local_dirs()
    sys.pycache_prefix = str(appinfo.local_dir("pycache"))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", sys.pycache_prefix)


# ------------------------------------------------------------------
# 2. 待機画面(基盤仕様書 2.2)
# ------------------------------------------------------------------
def write_loading_page() -> Path:
    """待機画面を、このPCのローカル領域へ書き出して返す。

    アプリ本体側(共有フォルダかもしれない場所)へ書かないのは 2.7 のとおり。
    ポートとログの場所を埋め込むので、**起動できなかったときに見る場所**が
    その画面に出ます。
    """
    from app import appinfo, logging_setup
    appinfo.ensure_local_dirs()
    template = (HERE / "loading.html").read_text(encoding="utf-8")
    page = (template
            .replace("{{APP_NAME}}", appinfo.display_name())
            .replace("{{VERSION}}", appinfo.version_label())
            .replace("{{APP_ID}}", appinfo.app_id())
            .replace("{{BASE_URL}}", appinfo.base_url())
            .replace("{{LOG_DIR}}", logging_setup.log_paths()["folder"]))
    target = appinfo.local_dir("runtime") / "loading.html"
    target.write_text(page, encoding="utf-8")
    return target


def open_browser(url: str, log) -> None:
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception as exc:                      # noqa: BLE001
        log.warning("ブラウザを開けませんでした: %s", exc)
        tell(f"ブラウザを開けませんでした。次のURLを開いてください:\n{url}")


# ------------------------------------------------------------------
# 起動
# ------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="エクセル → CSV 変換器 を起動します")
    parser.add_argument("--diagnose", action="store_true",
                        help="診断用。構成を表示し、コンソールにもログを出します")
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを開きません(調べるとき)")
    args = parser.parse_args(argv)

    problems = check_environment()
    if problems:
        tell("起動できません:\n\n  " + "\n  ".join(problems), title="起動できません")
        return EXIT_ENV

    prepare_local()
    from app import appinfo, intake, logging_setup, routes, settings_store

    log = logging_setup.launcher(console=args.diagnose)
    logging_setup.application(console=args.diagnose)
    logging_setup.quiet_vendor()

    log.info("=" * 60)
    log.info("起動します")
    for line in appinfo.describe().splitlines():
        log.info("  %s", line)
    log.info("  Python        : %s (%s)", sys.version.split()[0], sys.executable)
    if args.diagnose:
        print(appinfo.describe())
        print(f"Python        : {sys.version.split()[0]} ({sys.executable})")
        print(f"ログ          : {logging_setup.log_paths()['folder']}")

    # -- 多重起動の判定(基盤仕様書 2.4)---------------------------------
    routes.RUNTIME.stage = "実行環境を確認中"
    import launch_guard
    existing = launch_guard.probe()
    if existing.blocked:
        log.warning("起動を断りました: %s", existing.detail)
        text = launch_guard.refusal_text(existing)
        if existing.state == launch_guard.OURS:
            busy = (existing.info.get("screen") or {}).get("busy")
            if busy:
                tell(text, title="すでに起動しています")
            elif tell(text + "\n\n画面を開き直しますか?",
                      title="すでに起動しています", ask=True):
                open_browser(existing.url, log)
            return EXIT_ALREADY
        tell(text, title="起動できません")
        return EXIT_PORT

    # -- 待ち受け ------------------------------------------------------
    import server
    srv = server.Server()
    try:
        srv.bind()
    except OSError as exc:
        # 判定のあと、束縛までのあいだに誰かが先に取ることがあります。
        # **束縛の失敗が最後の歯止め**です
        log.warning("ポートを使えませんでした: %s", exc)
        tell(server.describe_bind_error(exc), title="起動できません")
        return EXIT_PORT

    stop_key = routes.RUNTIME.stop_key = os.urandom(16).hex()
    launch_guard.write_instance(server.instance_info(stop_key))

    if not args.no_browser:
        page = write_loading_page()
        log.info("待機画面: %s", page)
        open_browser(page.as_uri(), log)

    routes.RUNTIME.stage = "アプリを準備中"
    srv.start_serving()

    try:
        warm_up(log)
        routes.RUNTIME.stage = "接続を確認中"
        settings_store.apply_to_vendor(settings_store.load())
        intake.INTAKE.clear()                     # 前回の受け取りは持ち越さない
        srv.mark_ready()
        log.info("起動完了 %.1f秒 %s",
                 time.time() - routes.RUNTIME.started_at, appinfo.base_url())
        reason = srv.wait_until_stopped()
        log.info("終了しました: %s", reason)
    finally:
        launch_guard.clear_instance()
        intake.INTAKE.clear()
        log.info("片づけました")
    return EXIT_OK


def warm_up(log) -> None:
    """変換に使うものを先に読んでおく。

    最初の1回目だけ数秒かかることがあります(写してある日報管理ツールは
    42ファイルあります)。**押してから待たされる**より、起動中に済ませて
    おくほうが、待っている理由が分かります。
    """
    started = time.time()
    try:
        from nippou.logic import nippou_sheet, xlsx_sheet    # noqa: F401
        from nippou.reporting import csv_export              # noqa: F401
        from nippou.services import summary                  # noqa: F401

        from converter import run                            # noqa: F401
    except Exception as exc:                      # noqa: BLE001 - 起動は続ける
        log.warning("変換の準備で失敗しました(押したときに出ます): %s", exc)
        return
    log.info("変換の準備ができました(%.1f秒)", time.time() - started)


if __name__ == "__main__":
    raise SystemExit(main())
