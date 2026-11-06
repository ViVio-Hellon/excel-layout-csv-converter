#!/usr/bin/env python3
"""対象プロセスの確認と安全な停止(基盤仕様書 2.8)

**このアプリだけを止めます。** 同じPCで動く別のPythonアプリを巻き添えに
しないことが、このモジュールの唯一の目的です。

順序は仕様書のとおり:

    1. まずアプリ自身へ正常終了を要求する(`POST /api/shutdown`)
    2. 応答しないときだけ、記録したPIDを使う
    3. 落とす前に、そのPIDが**本当にこのアプリか**を確かめる
       ── **番号と起動時刻の両方**で見ます(`launch_guard.judge_recorded`)。
          再起動のあとは番号が使い回されるので、番号だけでは別の Python を
          終わらせてしまいます
    4. `python.exe` をプロセス名だけで一括終了することはしない

実行中の変換があるときは、既定では止めずに知らせます。中断してよいかは
利用者が決めます(`--force` で中断します)。

【止まったことを確かめてから言います】
「終わってもらいました」と出たのに Python が残っていた、という報告が
ありました。残っていると**そのフォルダを消せません**(配り直しは
フォルダを置き換えるので、ここで詰まります)。そこで:

- 言う前に確かめます。残っていれば**残っていると言い、終了コードも
  0 にしません**(`stop.bat` が次の手を出せます)
- 答えないとき(起動の途中で固まった、など)も「触りません」で
  終わらせず、**控えていたPIDの道**へ進みます
- **止まったと確かめるまで目印を消しません** ── 消すとPIDの控えが
  無くなり、次に打っても手がかりがありません
- ただし**別のプロセスだと分かったとき**(番号の使い回し)は、残っていた
  目印をその場で片付けます ── 撃つものが無いので、手がかりにもなりません

待機画面(本体を組み立てる前)も、いまは止める合図に答えます
(`boot_server.BootApp.shutdown`)。

使い方:

    python process_manager.py            止める
    python process_manager.py --status   状態を見るだけ
    python process_manager.py --force    変換中でも聞かずに止める
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import launch_guard                                # noqa: E402
from converter import app_config                   # noqa: E402
from converter.logging_utils import get_logger      # noqa: E402

log = get_logger("process_manager")

#: 正常終了を頼んでから諦めるまで(秒)
WAIT_SEC = 20.0


def status() -> int:
    found = launch_guard.check_existing()
    if found.state == launch_guard.OURS:
        health = found.health
        screen = health.get("screen") or {}
        job = health.get("job") or {}
        print(f"起動しています: PID {health.get('pid')} / {found.url}")
        if not health.get("ready"):
            # **ここが分かれ目です。** 本体を組み立てられず、理由の画面
            # だけが残っていることがあります(そのままではフォルダを
            # 消せません)。何が起きているかを先に言います
            print(f"  状態        : まだ準備できていません"
                  f"({health.get('stage') or '起動中'})")
            if health.get("startup_error"):
                print(f"  起動の失敗  : {health['startup_error']}")
                print("  → stop.bat で止められます")
        print(f"  画面        : {'開いています' if screen.get('busy') else '開いていません'}")
        print(f"  変換        : {job.get('state')}")
        return 0
    print({launch_guard.FREE: "起動していません"}.get(found.state, found.detail))
    lock = launch_guard.read_lock()
    if found.state != launch_guard.FREE and lock and lock.pid:
        verdict, why = launch_guard.judge_recorded(lock)
        print(f"  控えている PID: {lock.pid}"
              f"({launch_guard.lock_path()})")
        print(f"  その PID は   : {verdict} ── {why}")
    return 0


def _still_running() -> bool:
    return launch_guard.check_existing(timeout=1.0).state == launch_guard.OURS


def _looks_like_our_process(pid: int) -> bool:
    """そのPIDは本当にうちか。**確かめられなければ触りません。**"""
    if not pid:
        return False
    if not sys.platform.startswith("win"):
        return False                              # 現場はWindows。他所では撃たない
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:                             # noqa: BLE001
        return False
    name = out.split(",")[0].strip('" ').lower() if "," in out else ""
    return name in {"python.exe", "pythonw.exe", "py.exe", "pyw.exe"}


def _ask(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _stop_recorded(pid: int, lock=None, *, port_busy: bool = False) -> int:
    """答えない相手を、控えていたPIDで止める。**確かめてから言います。**

    `stop.bat` が「終わってもらいました」と言ったのに Python が残っていた、
    という報告がありました。言う前に確かめ、残っていれば**残っていると
    言います**(そのままではフォルダを消せないので、次の手が要ります)。

    **撃つ前に「本人か」を見ます。** 番号と起動時刻の両方が合わなければ
    撃ちません ── 再起動のあとは Windows が番号を使い回すので、まったく
    別の Python を終わらせてしまいます(`launch_guard.judge_recorded`)。
    別ものだと分かったら、残っていた**目印をその場で片付けます。**

    **止まったと確かめるまで目印(`instance.json`)は消しません。** 消すと
    PIDの控えが無くなり、次に `stop.bat` を打っても手がかりがありません。
    """
    if not pid:
        print("  控えている PID がありません"
              f"({launch_guard.lock_path()} が無い、または壊れています)。"
              "\n  タスクマネージャーで python.exe を確かめてください")
        return 1
    verdict, why = launch_guard.judge_recorded(lock, pid)
    if verdict == launch_guard.OTHER:
        # **撃ちません。** 番号が使い回されていて、別のプログラムです。
        # 残っていた目印は手がかりにならないので、ここで片付けます
        print(f"  {why}")
        launch_guard.remove_lock()
        print("  残っていた目印を片付けました(このアプリは動いていません)")
        if port_busy:
            print("  ポートは別のプログラムが使っています ── "
                  "config/app.json の server.port を確かめてください")
        return 1
    if not _looks_like_our_process(pid):
        print(f"  PID {pid} がこのアプリのものだと確かめられませんでした。"
              "\n  巻き添えを避けるため、強制終了はしません。"
              f"\n  タスクマネージャーで PID {pid} を確かめてください。")
        return 1
    if verdict == launch_guard.UNSURE:
        # 起動時刻を読めない端末。**python のプロセスであることだけ**
        # 確かめて撃ちます(これまでと同じ)。確かめられなかったことは言います
        print(f"  {why}")
    print(f"  控えていた PID {pid} で終了させます({why})")
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    time.sleep(1.0)
    if _looks_like_our_process(pid) or _still_running():
        print(f"まだ残っています(PID {pid})。"
              "\n  タスクマネージャーで終了させてください"
              "(残っているあいだは、このフォルダを消せません)。")
        return 1
    launch_guard.remove_lock()
    print("止まりました。")
    return 0


def stop(*, force: bool = False, timeout: float = WAIT_SEC) -> int:
    # **先に見ます。** `check_existing()` は繋がらなければその場で目印を
    # 消すので、あとで見ると「あったのかどうか」が分からなくなります
    had = launch_guard.lock_path().exists()
    found = launch_guard.check_existing()

    if found.state == launch_guard.FREE:
        # **残っていた目印はここで片付きます。** 前の版は「接続できない」で
        # 終わってしまい、ファイルを消していませんでした(ほかのツールの報告)
        launch_guard.remove_lock()
        if had:
            print("起動していません。残っていた目印を片付けました ── "
                  "Start.vbs で起動できます")
        else:
            print("起動していません(止めるものはありません)")
        return 0

    if found.state != launch_guard.OURS:
        # ポートは使われているのに、うちだと確かめられない。
        # **「触りません」で終わらせません** ── 起動の途中で固まった
        # ときもここへ来ます(そのまま残るとフォルダを消せません)
        lock = found.lock or launch_guard.read_lock()
        recorded = int(lock.pid) if lock and lock.pid else 0
        print(found.detail)
        if found.state == launch_guard.UNKNOWN:
            print("  答えないので、控えていた目印で止めます"
                  "(起動の途中で固まるとこうなります)")
            return _stop_recorded(recorded, lock, port_busy=True)
        # 別のアプリがポートを使っている。**そちらは触りません**
        print("  別のアプリなので触りません。"
              "config/app.json の server.port を確かめてください")
        verdict, why = launch_guard.judge_recorded(lock)
        if verdict == launch_guard.OTHER:
            # 番号の使い回し(再起動のあと)。**目印だけが残っていた**
            print(f"  {why}")
            launch_guard.remove_lock()
            print("  残っていた目印を片付けました")
            return 1
        if recorded and _looks_like_our_process(recorded):
            print(f"  ただし、控えていた PID {recorded} はまだ居ます ── "
                  f"こちらは止まっていません({why})")
            return 1
        return 0

    lock = found.lock or launch_guard.read_lock()
    token = lock.token if lock else ""
    stop_key = lock.stop_key if lock else ""
    pid = int(found.pid or (lock.pid if lock else 0))
    port = int((found.health.get("port") or app_config.port()))
    ready = bool(found.health.get("ready"))
    print(f"止めます: PID {pid} / {found.url}")
    if not ready:
        print("  まだ準備できていません"
              f"({found.health.get('stage') or '起動中'})"
              + (f" ── 起動の失敗: {found.health['startup_error']}"
                 if found.health.get("startup_error") else ""))

    # 1. まず本人へ頼む
    code, answer = launch_guard.request_shutdown(port, token, stop_key=stop_key,
                                                force=force)
    if code == 409 and answer.get("running"):
        print(f"\n{answer.get('reason')}: {answer.get('detail')}")
        if not force and not _ask("中止して終了しますか?"):
            print("止めませんでした。")
            return 2
        code, answer = launch_guard.request_shutdown(port, token,
                                                    stop_key=stop_key, force=True)
    if code in (401, 403):
        print("停止を断られました(出どころを確かめられませんでした)。"
              f"\n  {launch_guard.lock_path()} を読める利用者で実行してください")
    elif code == 503:
        # この版より古い待機画面は、止める合図に答えません(そのときは
        # 下のPIDの道で止めます)。**黙って20秒待つより、言います**
        print("  起動中の画面が『まだです』と答えました"
              "(古い版の待機画面は止める合図に答えません)")
    elif code == 0:
        print(f"  返事がありません: {answer.get('detail', '')}")

    # 2. 終わるのを待つ
    limit = time.time() + timeout
    while time.time() < limit:
        if not _still_running():
            print("止まりました。")
            launch_guard.remove_lock()
            return 0
        time.sleep(0.5)

    # 3. それでも残っていたら、控えたPIDだけを撃つ
    print("応答がありません。控えていたPIDで終了させます。")
    return _stop_recorded(pid, lock)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="バックエンドを安全に止めます")
    parser.add_argument("--status", action="store_true",
                        help="状態を見るだけ(止めません)")
    parser.add_argument("--force", action="store_true",
                        help="変換中でも聞かずに止めます")
    args = parser.parse_args(argv)
    return status() if args.status else stop(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
