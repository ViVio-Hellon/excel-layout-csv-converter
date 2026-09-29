#!/usr/bin/env python3
"""対象プロセスの確認と安全な停止(基盤仕様書 2.8)

**このアプリだけを止めます。** 同じPCで動く別のPythonアプリを巻き添えに
しないことが、このモジュールの唯一の目的です。

順序は仕様書のとおり:

    1. まずアプリ自身へ正常終了を要求する(`POST /api/shutdown`)
    2. 応答しないときだけ、記録したPIDを使う
    3. 落とす前に、そのPIDが**本当にこのアプリか**を確かめる
    4. `python.exe` をプロセス名だけで一括終了することはしない

実行中の変換があるときは、既定では止めずに知らせます。中断してよいかは
利用者が決めます(`--force` で中断します)。

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
        print(f"  画面        : {'開いています' if screen.get('busy') else '開いていません'}")
        print(f"  変換        : {job.get('state')}")
        return 0
    print({launch_guard.FREE: "起動していません"}.get(found.state, found.detail))
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


def stop(*, force: bool = False, timeout: float = WAIT_SEC) -> int:
    found = launch_guard.check_existing()
    if found.state != launch_guard.OURS:
        print({launch_guard.FREE: "起動していません(止めるものはありません)",
               }.get(found.state, found.detail + " ── 触りません"))
        launch_guard.remove_lock()
        return 0

    lock = found.lock or launch_guard.read_lock()
    token = lock.token if lock else ""
    stop_key = lock.stop_key if lock else ""
    pid = int(found.pid or (lock.pid if lock else 0))
    port = int((found.health.get("port") or app_config.port()))
    print(f"止めます: PID {pid} / {found.url}")

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
    if not _looks_like_our_process(pid):
        print(f"  PID {pid} がこのアプリのものだと確かめられませんでした。"
              "\n  巻き添えを避けるため、強制終了はしません。"
              f"\n  タスクマネージャーで PID {pid} を確かめてください。")
        return 1
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    time.sleep(1.0)
    launch_guard.remove_lock()
    alive = _still_running()
    print("終了させました。" if not alive else "まだ残っています。")
    return 1 if alive else 0


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
