#!/usr/bin/env python3
r"""Python側の起動開始点(基盤仕様書 2.5)

アプリ本体を読む前に実行環境を整え、多重起動を判定し、待ち受けを立てて
ブラウザを開きます。**業務機能はここに書きません。**

    Start.vbs(通常) / start.bat(診断)
        └─ start_app.py            ← ここ
             ├─ 実行環境の確認      (Python版数・vendor・書込権限・設定)
             ├─ launch_guard        (多重起動の判定)
             ├─ server + boot_server(待ち受け → まず待機画面)
             ├─ ブラウザを開く      (待機画面が出る)
             ├─ 準備                (変換に使うものを読む)
             └─ app へ引き継ぐ      (待ち受けは開き直さない)

【待機画面を先に出す】
起動で時間を食うのは、写してある日報管理ツール(42ファイル)の読み込み
です。そのあとに待機画面を出したら、「待たせるために見せるもの」を待たせて
いることになります ── 先に待ち受けだけ始めて、`boot_server` が待機画面を
出し、本体ができたら**同じ待ち受けのまま**差し替えます。

使い方:

    python start_app.py                通常どおり起動する
    python start_app.py --no-browser   ブラウザを開かない(検証用)
    python start_app.py --check        環境の確認だけして終わる(診断用)
    python start_app.py --diagnose     コンソールにもログを出す(診断用)
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 写してある日報管理ツール。**同じコードを通すため**にここに置いてあります
sys.path.insert(0, str(HERE / "vendor"))

#: 動かすのに要る Python
MIN_PYTHON = (3, 11)

#: 本体を組み立てられなかったとき、理由の画面を出したまま待つ上限(秒)。
#: **いつまでも残しません** ── 残ると、そのフォルダを消せなくなります
#: (配り直しは「フォルダを消して置き直す」なので、ここが効きます)。
#: 読むには十分な長さで、席を外しても片づく長さにしてあります
FAILED_BOOT_LIMIT_SEC = 10 * 60

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
# 実行環境の確認
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
    if not (HERE / "config" / "app.json").is_file():
        found.append(f"設定が見当たりません({HERE / 'config' / 'app.json'})")
    return found


def prepare_local() -> None:
    r"""ローカル領域を用意し、読込キャッシュをそちらへ向ける(基盤仕様書 2.7)。

    アプリ本体は共有フォルダに置かれることがあります。`__pycache__` を
    そこへ作ると、同期の競合と権限の問題を呼びます。**書くものは
    `%LOCALAPPDATA%` へ。**
    """
    from converter import app_config
    app_config.ensure_local_dirs()
    sys.pycache_prefix = str(app_config.local_dir("pycache"))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", sys.pycache_prefix)


def check_writable() -> list[str]:
    """ローカル領域へ書けるか。**書けないと、ログも設定も残せません。**"""
    from converter import app_config
    try:
        app_config.ensure_local_dirs()
        probe = app_config.local_dir("runtime") / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [f"ローカル領域へ書けません({app_config.local_root()}): {exc}"]
    return []


# ------------------------------------------------------------------
# 起動
# ------------------------------------------------------------------
def open_browser(url: str, log) -> None:
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception as exc:                      # noqa: BLE001
        log.warning("ブラウザを開けませんでした: %s", exc)
        tell(f"ブラウザを開けませんでした。次のURLを開いてください:\n{url}")


def warm_up(log) -> None:
    """変換に使うものを先に読んでおく。

    最初の1回だけ数秒かかることがあります(写してある日報管理ツールは
    42ファイル)。**押してから待たされる**より、起動中に済ませておくほうが、
    待っている理由が分かります。
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


def start(*, open_browser_flag: bool = True) -> int:
    """待ち受けを立てて、止められるまで動かす。"""
    import app as app_package
    import boot_server
    import launch_guard
    import server
    from converter import (app_config, idle_exit, intake, jobs,
                           user_settings)
    from converter import logging_utils
    from converter.logging_utils import get_logger

    # **ログの置き場所を最初に決めます。** 設定で指されていれば、起動の
    # 筋道からそちらへ出します(書けなければローカルのまま。理由も残ります)
    refused = logging_utils.use_folder(user_settings.load().log_dir)

    log = get_logger("launcher")
    log.info("起動します 版=%s pid=%s ログ=%s", app_config.version(),
             os.getpid(), logging_utils.folder())
    if refused:
        log.warning(refused)

    # -- 多重起動の判定(基盤仕様書 2.4)------------------------------
    existing = launch_guard.check_existing()
    if existing.blocked:
        log.warning("起動を断りました: %s", existing.detail)
        text = launch_guard.refusal_text(existing)
        if existing.state == launch_guard.OURS:
            busy = (existing.health.get("screen") or {}).get("busy")
            if busy:
                tell(text, title="すでに起動しています")
            elif tell(text + "\n\n画面を開き直しますか?",
                      title="すでに起動しています", ask=True):
                open_browser(existing.url, log)
            return EXIT_ALREADY
        tell(text, title="起動できません")
        return EXIT_PORT

    # -- 待ち受け(まず待機画面)--------------------------------------
    token = secrets.token_urlsafe(32)
    # 停止のための合図。**画面には渡しません** ── 起動トークンは画面の
    # HTMLに入るので、断られた2枚目のタブでも持ててしまいます
    stop_key = secrets.token_urlsafe(16)
    boot = boot_server.BootApp(app_config.port(), stop_key=stop_key)
    handover = boot_server.Handover(boot)
    srv = server.AppServer(handover)
    try:
        srv.bind()
    except OSError as exc:
        # 判定のあと、束縛までのあいだに誰かが先に取ることがあります。
        # **束縛の失敗が最後の歯止め**です
        log.warning("ポートを使えませんでした: %s", exc)
        tell(server.describe_bind_error(exc), title="起動できません")
        return EXIT_PORT

    launch_guard.write_lock(launch_guard.new_lock(port=srv.port, token=token,
                                                 stop_key=stop_key))
    # **待ち受けを始める前に、止められるようにします。** 本体を組み立て
    # られないと待機画面のまま残るので、ここで繋いでおかないと `stop.bat`
    # でも止まりません(実際にそうなった報告があります)
    boot.shutdown_hook = srv.request_stop
    srv.start_serving()
    if open_browser_flag:
        open_browser(app_config.base_url(), log)

    try:
        # -- 準備 ----------------------------------------------------
        handover.mark_stage("アプリを準備中", "prepare")
        warm_up(log)

        if srv.stopping:
            # 準備の途中で止めてと言われた。**続きは組み立てません** ──
            # 画面を出さずに終わるので、ここから先は誰も使いません
            log.info("準備中に停止を頼まれました。引き継ぎません")
            log.info("終了しました: %s", srv.wait_until_stopped())
            return EXIT_OK

        handover.mark_stage("接続を確認中", "connect")
        user_settings.apply_to_vendor(user_settings.load())
        intake.clear_all()                        # 前回の受け取りは持ち越さない

        application = app_package.create_app(token=token, port=srv.port,
                                             stop_key=stop_key)
        application.shutdown_hook = srv.request_stop
        application.idle = idle_exit.IdleExit(busy=lambda: jobs.RUNNER.running,
                                              stop=srv.request_stop)
        application.idle.start()

        # -- 引き継ぎ(待ち受けは開き直さない)-------------------------
        handover.install(application)
        application.mark_ready()
        log.info("起動完了 %.1f秒 %s",
                 time.time() - boot.started_at, app_config.base_url())

        reason = srv.wait_until_stopped()
        log.info("終了しました: %s", reason)
    except Exception as exc:                      # noqa: BLE001 - 理由を出す
        log.exception("起動の途中で失敗しました")
        handover.mark_error(str(exc))
        # **知らせは別の糸で出します。** Windows の MessageBox は押される
        # まで戻らないので、ここで待つと「止めてと言われても、誰かが
        # OKを押すまで終われない」プロセスになります
        threading.Thread(
            target=tell, args=(f"起動できませんでした: {exc}",),
            kwargs={"title": "起動できません"},
            name="tell", daemon=True).start()
        # **いつまでも残しません。** 理由を読む時間は置きますが、置き
        # 去りにされたら自分で閉じます(残るとフォルダを消せません)
        closer = threading.Timer(
            FAILED_BOOT_LIMIT_SEC, srv.request_stop,
            args=(f"起動に失敗した画面を閉じました"
                  f"({FAILED_BOOT_LIMIT_SEC // 60}分)",))
        closer.daemon = True
        closer.start()
        log.warning("理由の画面を出したまま待ちます(%d分で自分から閉じます)。"
                    "stop.bat でも止められます", FAILED_BOOT_LIMIT_SEC // 60)
        reason = srv.wait_until_stopped()
        closer.cancel()
        log.info("終了しました: %s", reason)
        return EXIT_ENV
    finally:
        launch_guard.remove_lock()
        intake.clear_all()
        log.info("片づけました")
    return EXIT_OK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="エクセル → CSV 変換器 を起動します")
    parser.add_argument("--diagnose", action="store_true",
                        help="診断用。構成を表示し、コンソールにもログを出します")
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを開きません(検証用)")
    parser.add_argument("--check", action="store_true",
                        help="環境の確認だけして終わります(診断用)")
    args = parser.parse_args(argv)

    problems = check_environment()
    if problems:
        tell("起動できません:\n\n  " + "\n  ".join(problems), title="起動できません")
        return EXIT_ENV

    prepare_local()
    from converter import app_config, logging_utils

    if args.diagnose or args.check:
        logging_utils.use_console(True)
    log = logging_utils.get_logger("launcher")
    logging_utils.quiet_vendor()

    problems = check_writable()
    if problems:
        tell("起動できません:\n\n  " + "\n  ".join(problems), title="起動できません")
        return EXIT_ENV

    log.info("=" * 60)
    log.info("起動します")
    for line in app_config.describe().splitlines():
        log.info("  %s", line)
    log.info("  Python        : %s (%s)", sys.version.split()[0], sys.executable)

    if args.check:
        print(app_config.describe())
        print(f"Python        : {sys.version.split()[0]} ({sys.executable})")
        print(f"ログ          : {logging_utils.log_paths()['folder']}")
        found = __import__("launch_guard").check_existing()
        print(f"いまの状態    : {found.state}"
              + (f" ── {found.detail}" if found.detail else ""))
        return EXIT_OK

    return start(open_browser_flag=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
