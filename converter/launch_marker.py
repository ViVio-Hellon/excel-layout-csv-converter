"""いまどちらが動いているか ── **ブラウザ版とデスクトップ版は同時に動きません**

【なぜ要るのか】
2つ動くと、同じ設定を2つのプロセスが書き、片方の変換がもう片方の出力先を
上書きし、どちらのログを見ればよいかも分からなくなります。**同じアプリは
1つだけ**という約束は、運び方が2通りになっても変わりません。

    ブラウザ版     ポートで待ち受ける  → 印は `runtime/instance.json`(`launch_guard`)
    デスクトップ版 ポートを使わない    → 印は `runtime/desktop.json`(ここ)

【どう見分けるか ── ポートを使わない側がいるので、印で見ます】
デスクトップ版はポートを使わないので、「ポートが答えるか」では見つかりません。
そこで**印に書いた番号(PID)とプロセスの起動時刻**で、その印を書いた
プロセスがまだ動いているかを見ます(`launch_guard.same_process`)。

**番号だけでは足りません。** 再起動のあとは Windows が番号を使い回すので、
まったく別のプログラムが同じ番号を持っていることがあります ── 1.11.3 で
入れた見分けをそのまま使います。

【あとから開いたほうが止まります】
先に動いているほうは触りません(変換の途中で落とさないため)。

    デスクトップ版を開いた + ブラウザ版が動いている → 窓に理由を出して終わる
    ブラウザ版を開いた + デスクトップ版が動いている → 起動しない(ブラウザも開かない)

止めたいときは `stop.bat`(両方を見て、動いているほうを止めます)。

【このモジュールはソケットを開きません】
デスクトップ版の道で使うので、**ファイルを読むだけ**にしてあります
(`tests/test_bridge.py` がソケットを禁止して確かめます)。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from . import app_config
from .logging_utils import get_logger

log = get_logger("launch_marker")

#: デスクトップ版の印。`.lock` にしないのは、外枠(exe)が握る OS のロック
#: (`runtime/desktop.lock`)と混ざらないようにするため
DESKTOP_FILE = "desktop.json"

#: 画面に出す言葉。**2か所で別々に書かない**(言い方がぶれます)
BROWSER_RUNNING = "ブラウザ版が起動しています"
DESKTOP_RUNNING = "デスクトップ版が起動しています"
WHAT_TO_DO_BROWSER = ("先にブラウザ版の画面で「終了」するか、stop.bat を"
                      "実行してから、もう一度開いてください")
WHAT_TO_DO_DESKTOP = ("先にデスクトップ版の窓を閉じるか、stop.bat を"
                      "実行してから、もう一度起動してください")


def desktop_path() -> Path:
    return app_config.local_dir("runtime") / DESKTOP_FILE


# ------------------------------------------------------------------
# デスクトップ版の印
# ------------------------------------------------------------------
def write_desktop(*, pid: int, exe_pid: int = 0) -> Optional[Path]:
    """デスクトップ版が動き始めた。**書けなくても起動は止めません。**"""
    import launch_guard

    path = desktop_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "app_id": app_config.app_id(),
            "mode": "desktop",
            "version": app_config.version(),
            "pid": int(pid),
            # **プロセスの起動時刻。** 番号は使い回されるので、これが要ります
            "proc_started_at": launch_guard.process_started_at(int(pid)) or 0.0,
            "exe_pid": int(exe_pid),
            "exe_started_at": (launch_guard.process_started_at(int(exe_pid))
                               or 0.0) if exe_pid else 0.0,
            "started_at": time.time(),
            "app_root": str(app_config.APP_ROOT),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError as exc:                        # noqa: BLE001 - 起動は続ける
        log.warning("デスクトップ版の印を書けませんでした(%s): %s", path, exc)
        return None


def read_desktop() -> Optional[dict]:
    """印の中身(読めなければ `None`)。**動いているかは見ません。**"""
    try:
        found = json.loads(desktop_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def remove_desktop(*, expect: Optional[dict] = None) -> None:
    """自分の印だけ消します。

    `expect` を渡すと「読んだときと同じ印なら消す」になります
    (`stop.bat` が残った印を片付けるとき ── あとから起動した別の
    デスクトップ版の印を消さないため)。
    """
    path = desktop_path()
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        if expect is not None:
            mine = (int(found.get("pid", 0)) == int(expect.get("pid", 0))
                    and found.get("started_at") == expect.get("started_at"))
        else:
            mine = int(found.get("pid", 0)) == os.getpid()
        if mine:
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


# ------------------------------------------------------------------
# どちらが動いているか
# ------------------------------------------------------------------
def desktop_running() -> Optional[dict]:
    """デスクトップ版が動いていれば、その印 + `detail`。動いていなければ `None`。

    印が残っていても、書いた Python がもう居なければ動いていません
    (落ちたあと・PCを落としたあと)。**番号と起動時刻の両方**で見ます。
    """
    import launch_guard

    found = read_desktop()
    if not found:
        return None
    if str(found.get("app_id") or "") != app_config.app_id():
        return None                               # よそのアプリの印
    verdict, why = launch_guard.same_process(int(found.get("pid") or 0),
                                             float(found.get("proc_started_at")
                                                   or 0.0))
    if verdict == launch_guard.OTHER:
        return None
    # 確かめられない端末(起動時刻が読めない)では、**居るものとみなします**
    # ── 2つ動かすほうが困るので(`UNSURE` は「たぶん居る」側に倒す)
    detail = f"PID {found.get('pid')}"
    if found.get("exe_pid"):
        detail += f" / 窓 PID {found.get('exe_pid')}"
    return {**found, "detail": detail, "verdict": verdict, "why": why}


def browser_running() -> Optional[dict]:
    """ブラウザ版が動いていれば、その印 + `detail`。動いていなければ `None`。

    **ソケットを開きません**(デスクトップ版の道から呼ぶため)。
    ポートの応答ではなく、印(`instance.json`)に書かれた番号と起動時刻で
    見ます ── 固まっていても「動いている」と見ます(2つ動かすより安全)。
    """
    import launch_guard

    lock = launch_guard.read_lock()
    if lock is None or not lock.pid:
        return None
    if lock.app_id and lock.app_id != app_config.app_id():
        return None
    verdict, why = launch_guard.judge_recorded(lock)
    if verdict == launch_guard.OTHER:
        return None
    return {"mode": "browser", "pid": lock.pid, "port": lock.port,
            "url": lock.url, "version": lock.version,
            "detail": f"PID {lock.pid} / {lock.url or ''}".strip(" /"),
            "verdict": verdict, "why": why}


def other_mode_running(mode: str) -> Optional[dict]:
    """**自分と違う側**が動いているか。`mode` は `"browser"` / `"desktop"`。"""
    if mode == "desktop":
        return browser_running()
    if mode == "browser":
        return desktop_running()
    raise ValueError(f"知らないモードです: {mode!r}")


def refusal(mode: str, found: dict) -> tuple[str, str]:
    """あとから開いたほうに出す `(見出し, どうすればよいか)`。"""
    if mode == "desktop":
        return BROWSER_RUNNING, WHAT_TO_DO_BROWSER
    return DESKTOP_RUNNING, WHAT_TO_DO_DESKTOP
