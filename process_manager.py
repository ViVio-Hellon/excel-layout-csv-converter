r"""安全な停止(基盤仕様書 2.8)

**対象のアプリだけを止めます。** 同じPCで動いている別のPythonアプリを
巻き込まないために、次の順で進めます。

    1. 起動確認APIで「それはうちか」を確かめる   `app_id` の照合
    2. まずアプリ自身へ正常終了を頼む             /api/shutdown
    3. 変換中なら、止めてよいか確かめる           (勝手に途中で切らない)
    4. 応答が無いときだけ、控えたPIDを使う         instance.json
    5. 強制終了の前に、そのPIDが本当にうちか見る   実行ファイル名を確認

`python.exe` をまとめて落とすようなことはしません ── 現場のPCでは他の
ツールも Python で動いていることがあります。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import appinfo                            # noqa: E402
import launch_guard                                # noqa: E402


def _post(path: str, payload: dict, timeout: float = 5.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        appinfo.base_url() + path.lstrip("/"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:                          # noqa: BLE001
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return 0, {"detail": str(exc)}


def status() -> int:
    found = launch_guard.probe()
    if found.state == launch_guard.OURS:
        info = found.info
        print(f"起動しています: PID {info.get('pid')} / {found.url}")
        print(f"  画面        : {'開いています' if (info.get('screen') or {}).get('busy') else '開いていません'}")
        job = info.get("job") or {}
        print(f"  変換        : {job.get('state')}")
        return 0
    print({launch_guard.FREE: "起動していません",
           launch_guard.FOREIGN: found.detail,
           launch_guard.UNKNOWN: found.detail}.get(found.state, found.state))
    return 0


def _still_running() -> bool:
    return launch_guard.probe(timeout=1.0).state == launch_guard.OURS


def _is_our_process(pid: int) -> bool:
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


def stop(force: bool = False, timeout: float = 20.0) -> int:
    found = launch_guard.probe()
    if found.state != launch_guard.OURS:
        print({launch_guard.FREE: "起動していません(止めるものはありません)",
               launch_guard.FOREIGN: found.detail + " ── 触りません",
               launch_guard.UNKNOWN: found.detail + " ── 触りません",
               }.get(found.state, found.state))
        launch_guard.clear_instance()
        return 0

    instance = launch_guard.read_instance()
    stop_key = str(instance.get("stop_key") or "")
    pid = int(found.pid or instance.get("pid") or 0)
    print(f"止めます: PID {pid} / {found.url}")

    # 1. まず本人へ頼む
    code, answer = _post("/api/shutdown", {"stop_key": stop_key, "force": force})
    if code == 409 and answer.get("running"):
        print(f"\n{answer.get('reason')}: {answer.get('detail')}")
        if not force and not _ask("中止して終了しますか?"):
            print("止めませんでした。")
            return 2
        code, answer = _post("/api/shutdown", {"stop_key": stop_key, "force": True})
    if code == 403:
        print("停止を断られました(出どころを確かめられませんでした)。"
              f"\n  {appinfo.instance_path()} を読める利用者で実行してください")

    # 2. 終わるのを待つ
    limit = time.time() + timeout
    while time.time() < limit:
        if not _still_running():
            print("止まりました。")
            launch_guard.clear_instance()
            return 0
        time.sleep(0.5)

    # 3. それでも残っていたら、控えたPIDだけを撃つ
    print("応答がありません。控えていたPIDで終了させます。")
    if not _is_our_process(pid):
        print(f"  PID {pid} がこのアプリのものだと確かめられませんでした。"
              "\n  巻き添えを避けるため、強制終了はしません。"
              f"\n  タスクマネージャーで PID {pid} を確かめてください。")
        return 1
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    time.sleep(1.0)
    launch_guard.clear_instance()
    print("終了させました。" if not _still_running() else "まだ残っています。")
    return 0 if not _still_running() else 1


def _ask(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="バックエンドを安全に止めます")
    parser.add_argument("action", nargs="?", default="stop",
                        choices=["stop", "status"])
    parser.add_argument("--force", action="store_true",
                        help="変換中でも聞かずに止めます")
    args = parser.parse_args(argv)
    return status() if args.action == "status" else stop(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
