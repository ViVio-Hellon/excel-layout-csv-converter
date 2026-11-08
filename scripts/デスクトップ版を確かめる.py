#!/usr/bin/env python3
"""デスクトップ版(exe)を本当に起動して、通し確認する道具

    python scripts/デスクトップ版を確かめる.py [--exe <パス>]
    # Linux では窓が要るので: xvfb-run -a python scripts/デスクトップ版を確かめる.py

見るのは**人が目で見るしかないもの以外**の5つです:

    ① exe を起動すると、Python(`bridge.py`)が子として起きる
    ② その Python が画面を返している(ログに「起動完了(デスクトップ版)」)
    ③ **どのプロセスもポートで待ち受けていない**(ここが移行の肝)
    ④ デスクトップ版の印が置かれる(ブラウザ版はこれを見て起動を断る)
    ⑤ exe を止めると Python も終わり、印も片付く(取り残さない)

窓の見た目・ドラッグ&ドロップ・印刷は人が見ます
(`docs/デスクトップ版.md` の「確かめきれていないこと」)。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_exe(given: str) -> Path:
    if given:
        return Path(given)
    for name in ("ExcelCsvConverter", "ExcelCsvConverter.exe"):
        for where in ("release", "debug"):
            found = ROOT / "src-tauri" / "target" / where / name
            if found.is_file():
                return found
    print("exe が見つかりません。先に次を実行してください:\n"
          "  cd src-tauri && cargo build", file=sys.stderr)
    raise SystemExit(2)


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def ports_of(pids: list[int]) -> list[str]:
    """そのプロセスたちが**待ち受けている**ポート(あれば名前を返す)。"""
    found = []
    try:
        out = subprocess.run(["ss", "-lntpH"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return found
    for line in out.splitlines():
        for pid in pids:
            if f"pid={pid}," in line:
                found.append(line.strip())
    return found


def children_of(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in out.split() if line.isdigit()]


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="デスクトップ版を通しで確かめます")
    parser.add_argument("--exe", default="", help="exe の場所(既定: src-tauri/target/…)")
    parser.add_argument("--wait", type=float, default=60.0, help="待つ上限(秒)")
    args = parser.parse_args(argv)

    exe = find_exe(args.exe)
    local = Path(tempfile.mkdtemp(prefix="xlcsv_desktop_"))
    env = dict(os.environ, XLCSV_LOCAL_DIR=str(local), XLCSV_ROOT=str(ROOT),
               PYTHONPATH=str(ROOT / "vendor"))
    marker = local / "runtime" / "desktop.json"
    bad = []

    print(f"exe          : {exe}")
    print(f"ローカル領域 : {local}")
    child = subprocess.Popen([str(exe)], cwd=str(ROOT), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    try:
        # ① 印が置かれるまで待つ(= Python が起きて、準備まで進んだ)
        limit = time.time() + args.wait
        while time.time() < limit and not marker.is_file():
            if child.poll() is not None:
                print("× exe が終わってしまいました:\n"
                      + (child.stdout.read() or "")[-2000:])
                return 1
            time.sleep(0.25)
        if not marker.is_file():
            bad.append("① Python が起きません(印が置かれない)")
            print(f"  exe の言ったこと: {(child.stdout.read() or '')[-1500:]}")
        else:
            found = json.loads(marker.read_text(encoding="utf-8"))
            print(f"① Python が起きた       : PID {found['pid']} "
                  f"(窓 PID {found.get('exe_pid')})")
            if int(found.get("exe_pid") or 0) != child.pid:
                bad.append(f"① 窓の番号が合いません({found.get('exe_pid')} ≠ {child.pid})")
            if not found.get("proc_started_at"):
                bad.append("① プロセスの起動時刻が印に無い(番号の使い回しを見分けられない)")

            # ② ログに「起動完了」(本体が組み上がるまで待ちます)
            def log_text() -> str:
                logs = sorted((local / "logs").glob("*_app.log"))
                return (logs[0].read_text(encoding="utf-8", errors="replace")
                        if logs else "")

            def wait_for(word: str, seconds: float = 30.0) -> str:
                limit = time.time() + seconds
                text = log_text()
                while time.time() < limit and word not in text:
                    time.sleep(0.25)
                    text = log_text()
                return text

            text = wait_for("起動完了(デスクトップ版)")
            logs = sorted((local / "logs").glob("*_app.log"))
            if "起動完了(デスクトップ版)" in text:
                print("② 本体が組み上がった   : ログに「起動完了(デスクトップ版)」")
            else:
                bad.append("② ログに「起動完了(デスクトップ版)」がありません")

            # ②-2 **窓が本当に画面を読み込んだか**(真っ白との切り分け)
            text = wait_for("画面から最初の要求", 20.0)
            if "画面から最初の要求" in text:
                line = [l for l in text.splitlines()
                        if "画面から最初の要求" in l][0]
                print(f"②-2 窓が画面を読み込んだ: {line.split('bridge: ')[-1]}")
            else:
                bad.append("②-2 窓から要求が来ていません(窓が真っ白のおそれ)")

            # ②-3 **画面が使える状態になったか**(覆いが出ていないか)。
            # 合言葉が食い違うと、窓は出るのに「この操作は受け付けられません」
            # の覆いが出ます ── 窓の絵を見なくても、ここで分かります
            text = wait_for("画面を渡しました", 20.0)
            if "画面を渡しました" in text:
                print("②-3 画面が使える        : 「画面を渡しました」(覆いは出ていない)")
            else:
                bad.append("②-3 画面を受け取れていません"
                           "(窓に『この操作は受け付けられません』が出ているはず)")

            # ③ **どのプロセスもポートで待ち受けていない**
            pids = [child.pid, int(found["pid"])] + children_of(child.pid)
            holding = ports_of(pids)
            busy = listening(8741)
            print(f"③ ポートで待ち受けない : 8741 に居ない={not busy} "
                  f"／ 待ち受けているもの={holding or 'なし'}")
            if busy:
                bad.append("③ 8741 で誰かが待ち受けています")
            if holding:
                bad.append(f"③ 待ち受けているプロセスがあります: {holding}")

            # ④ 印の中身(ブラウザ版がこれを見る)
            sys.path.insert(0, str(ROOT))
            os.environ["XLCSV_LOCAL_DIR"] = str(local)
            from converter import launch_marker
            if launch_marker.desktop_running():
                print("④ 印が置かれている     : ブラウザ版はこれを見て断ります")
            else:
                bad.append("④ 印から「動いている」と見えません")

        # ⑤ 止めると Python も終わる
        python_pid = int(json.loads(marker.read_text(encoding="utf-8"))["pid"]) \
            if marker.is_file() else 0
        child.send_signal(signal.SIGTERM)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            bad.append("⑤ exe が止まりません")
        left = True
        limit = time.time() + 15
        while time.time() < limit:
            try:
                os.kill(python_pid, 0)
            except OSError:
                left = False
                break
            time.sleep(0.25)
        print(f"⑤ 止めたら片付く       : Python が残っていない={not left} "
              f"／ 印が消えた={not marker.is_file()}")
        if left:
            bad.append("⑤ Python が残りました(取り残し)")
        if marker.is_file():
            bad.append("⑤ 印が残りました")
    finally:
        if child.poll() is None:
            child.kill()

    print()
    if bad:
        print("通らなかったもの:")
        for one in bad:
            print(f"  × {one}")
        return 1
    print("すべて通りました(窓の見た目・ドラッグ&ドロップ・印刷は人が見ます)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
