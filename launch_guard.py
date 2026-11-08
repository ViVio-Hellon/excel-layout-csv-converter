"""多重起動の防止とインスタンス管理(基盤仕様書 2.4)

同じアプリが2つ起動すると、同じ設定を2つのプロセスが書き、片方の変換が
もう片方の出力先を上書きし、どちらのログを見ればよいかも分からなくなり
ます。**同じアプリは1つだけ**にします。

【どう判定するか】
目印のファイルだけでは足りません ── 強制終了されたあとのPIDファイルは
残ったままで、「起動しているのに起動していない」ことになります。そこで
2段構えにします:

    1. PIDのプロセスが居るか
    2. そのポートの `/api/health` が**同じ `app_id`** を返すか

1だけでは足りません(PIDは使い回されます)。2だけでも足りません
(別のアプリがHTTPを返していることがあります)。

**「起動中」の判断に目印のPIDは使いません。** ポートが受け付けなければ
その場で目印を消して「空いている」と答えます ── ほかのツールで
「再起動のあとPIDが使い回され、いつまでも起動中と判断して止まる」という
不具合がありました。こちらは応答で確かめるので、そうはなりません。

【PIDを撃つときは、番号だけでは足りません】(1.11.3)
残った目印のPIDを**終了させる**ときだけは、番号の使い回しが怖い ──
再起動のあと、まったく別の Python が同じ番号を持っていることがあります。
そこで目印に**プロセスが始まった時刻**も書き、**番号と起動時刻の両方が
合うときだけ**「うちのプロセス」とみなします(`judge_recorded`)。
前の版が書いた目印には起動時刻が入っていないので、そのときは
**「目印を書いた時刻より後に始まったプロセスは、書いた本人ではない」**
で見ます。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from converter import app_config
from converter.logging_utils import get_logger

log = get_logger("guard")

#: 判定の結果
FREE = "free"            # 空いている。起動してよい
OURS = "ours"            # このアプリがすでに起動している
FOREIGN = "foreign"      # 別のアプリがポートを使っている
UNKNOWN = "unknown"      # 繋がるが、何なのか答えない

HEALTH_TIMEOUT_SEC = 2.0


@dataclass
class LockInfo:
    """起動中の目印(`runtime/instance.json`)。

    **判定の主役ではありません。** 生きているかは応答で確かめます。
    ここに残すのは、止めるときに要るもの ── PID と、正常終了を頼むための
    **停止の合図**(`stop_key`)です。起動トークンのほうは画面のHTMLにも
    入るので、停止の判断には使いません(断られた2枚目のタブも持っている
    ため)。この目印はローカル領域の中にしかなく、ブラウザからは読めません。
    """

    pid: int = 0
    port: int = 0
    token: str = ""
    #: 停止のための合図。**画面には渡りません**(こことアプリの中だけ)
    stop_key: str = ""
    url: str = ""
    app_id: str = ""
    version: str = ""
    python: str = ""
    app_root: str = ""
    started_at: float = field(default_factory=time.time)
    #: **プロセスが始まった時刻**(OSが言う値。unix秒)。番号の使い回しを
    #: 見分けるためのもので、読めない端末では 0 です
    proc_started_at: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardResult:
    state: str = FREE
    detail: str = ""
    url: str = ""
    pid: int = 0
    health: dict = field(default_factory=dict)
    lock: Optional[LockInfo] = None

    @property
    def running(self) -> bool:
        return self.state == OURS

    @property
    def blocked(self) -> bool:
        """起動してはいけない状態(すでにある / ポートが塞がっている)。"""
        return self.state != FREE


# ------------------------------------------------------------------
# 目印
# ------------------------------------------------------------------
def lock_path() -> Path:
    return app_config.instance_path()


def write_lock(info: LockInfo) -> Path:
    app_config.ensure_local_dirs()
    path = lock_path()
    path.write_text(json.dumps(info.as_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def read_lock() -> Optional[LockInfo]:
    try:
        raw = json.loads(lock_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    known = {k: raw.get(k) for k in LockInfo().as_dict() if raw.get(k) is not None}
    return LockInfo(**known)


def remove_lock() -> None:
    try:
        lock_path().unlink()
    except (FileNotFoundError, OSError):
        pass


def new_lock(*, port: int, token: str, stop_key: str = "") -> LockInfo:
    """いまのプロセスぶんの目印を組み立てる。"""
    import sys
    return LockInfo(pid=os.getpid(), port=port, token=token,
                    stop_key=stop_key,
                    url=app_config.base_url(), app_id=app_config.app_id(),
                    version=app_config.version(), python=sys.executable,
                    app_root=str(app_config.APP_ROOT),
                    proc_started_at=process_started_at(os.getpid()) or 0.0)


# ------------------------------------------------------------------
# そのPIDは、目印を書いた本人か(番号の使い回しを見分ける)
# ------------------------------------------------------------------
#: `judge_recorded` の答え
SAME = "同じ"            # 番号も起動時刻も合う。**うちのプロセス**
OTHER = "別もの"         # 居ない、または起動時刻が合わない。**撃ちません**
UNSURE = "確かめられない"  # 起動時刻を読めない端末(撃つかどうかは呼ぶ側が決める)

#: 起動時刻の許し(秒)。OSが言う値と、こちらが書いた時刻のずれを見るだけ
START_TOLERANCE = 2.0


def process_started_at(pid: int) -> Optional[float]:
    """そのプロセスが始まった時刻(unix秒)。**読めなければ `None`。**

    追加ライブラリは使いません(`psutil` は入れられない端末があります):

        Windows … `GetProcessTimes`(`ctypes` だけで読めます)
        Linux   … `/proc/<pid>/stat` の22番目 + 起動時刻(`/proc/stat` の btime)

    `None` は「読めなかった」で、**「居ない」ではありません** ── 別の
    利用者のプロセスは開けないことがあります。
    """
    if not pid or pid <= 0:
        return None
    import sys
    if sys.platform.startswith("win"):
        return _started_at_windows(int(pid))
    return _started_at_linux(int(pid))


def _started_at_windows(pid: int) -> Optional[float]:
    """`OpenProcess` → `GetProcessTimes`。**FILETIME を unix秒へ。**"""
    try:
        import ctypes
        from ctypes import wintypes

        #: 問い合わせだけの権限。終了させる権限は要りません
        QUERY_LIMITED = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(QUERY_LIMITED, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel_t), ctypes.byref(user_t))
            if not ok:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # FILETIME は 1601-01-01 からの 100ナノ秒。unix紀元までの差を引く
            return (ticks - 116_444_736_000_000_000) / 10_000_000
        finally:
            kernel32.CloseHandle(handle)
    except Exception:                             # noqa: BLE001 - 読めないだけ
        return None


def _started_at_linux(pid: int) -> Optional[float]:
    """`/proc` から。**試験と、現場以外の端末のために持っています。**"""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            fields = handle.read().rsplit(b")", 1)[1].split()
        ticks = float(fields[19])                 # 22番目(括弧の後ろから20番目)
        hertz = os.sysconf("SC_CLK_TCK") or 100
        for line in open("/proc/stat", encoding="ascii"):
            if line.startswith("btime "):
                return float(line.split()[1]) + ticks / hertz
    except Exception:                             # noqa: BLE001 - 読めないだけ
        return None
    return None


def lock_written_at() -> Optional[float]:
    """目印のファイルが書かれた時刻。**前の版の目印を見分けるのに使います。**"""
    try:
        return lock_path().stat().st_mtime
    except OSError:
        return None


def same_process(pid: int, proc_started_at: float) -> tuple[str, str]:
    """その番号のプロセスは、控えてある起動時刻のものか。`(答え, 理由)`。

    **番号だけでは足りません。** 再起動のあとは Windows が番号を使い回す
    ので、まったく別のプログラムが同じ番号を持っていることがあります
    (デスクトップ版の印 `desktop.json` もこれで見ます)。
    """
    if not pid:
        return OTHER, "番号がありません"
    started = process_started_at(int(pid))
    if started is None:
        return UNSURE, f"PID {pid} の起動時刻を読めませんでした"
    if not proc_started_at:
        return UNSURE, f"PID {pid} の起動時刻が控えてありません"
    if abs(started - float(proc_started_at)) <= START_TOLERANCE:
        return SAME, f"PID {pid} は起動時刻も合います"
    return OTHER, (f"PID {pid} は別のプロセスです"
                   f"(起動時刻が {abs(started - float(proc_started_at)):.0f}秒 "
                   "ちがいます ── 番号が使い回されています)")


def judge_recorded(lock: Optional[LockInfo], pid: int = 0) -> tuple[str, str]:
    """控えてあるPIDは、その目印を書いた本人か。`(答え, 理由)` を返します。

    **終了させる前に必ず通します。** 番号だけで撃つと、再起動のあとで
    その番号を持った**別の Python を終わらせて**しまいます。

        起動時刻が目印にある   → 両方が合うときだけ `SAME`
        起動時刻が目印に無い   → **目印を書いた時刻より後に始まっていれば
                                 別もの**(前の版が書いた目印)
        起動時刻が読めない     → `UNSURE`(呼ぶ側が決めます)
    """
    pid = int(pid or (lock.pid if lock else 0) or 0)
    if not pid:
        return OTHER, "控えているPIDがありません"
    recorded = float(lock.proc_started_at) if lock else 0.0
    if recorded:
        return same_process(pid, recorded)
    started = process_started_at(pid)
    if started is None:
        return UNSURE, f"PID {pid} の起動時刻を読めませんでした"
    # 前の版の目印(起動時刻が入っていない)
    written = lock_written_at()
    if written is None:
        return UNSURE, f"PID {pid} の起動時刻を目印と比べられませんでした"
    if started > written + START_TOLERANCE:
        return OTHER, (f"PID {pid} は目印を書いたあとに始まったプロセスです"
                       "(前の版の目印 ── 番号が使い回されています)")
    return SAME, f"PID {pid} は目印を書いた時刻より前から動いています"


# ------------------------------------------------------------------
# 生きているか
# ------------------------------------------------------------------
def is_port_accepting(port: int, *, host: str = "", timeout: float = 0.6) -> bool:
    host = host or app_config.host()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def probe_health(port: int, *, timeout: float = HEALTH_TIMEOUT_SEC) -> Optional[dict]:
    """そのポートの起動確認APIを叩く。答えなければ `None`。"""
    url = f"http://{app_config.host()}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            found = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return found if isinstance(found, dict) else None


def is_our_app(health: Optional[dict]) -> bool:
    """**HTTPが返ったことと、うちが起動していることは別。**"""
    return bool(health and str(health.get("app_id")) == app_config.app_id())


# ------------------------------------------------------------------
# 判定
# ------------------------------------------------------------------
def check_existing(*, timeout: float = HEALTH_TIMEOUT_SEC) -> GuardResult:
    """すでに起動しているか。**応答で確かめます**(目印だけで決めません)。"""
    port = app_config.port()
    if not is_port_accepting(port):
        remove_lock()                             # 残っていた目印は嘘なので消す
        return GuardResult(state=FREE)

    health = probe_health(port, timeout=timeout)
    if health is None:
        return GuardResult(
            state=UNKNOWN, url=app_config.base_url(),
            detail=f"ポート {port} は使われていますが、何のアプリか答えません")
    if not is_our_app(health):
        name = health.get("display_name") or health.get("app_id")
        return GuardResult(state=FOREIGN, url=app_config.base_url(), health=health,
                           detail=f"ポート {port} は別のアプリが使っています({name})")
    return GuardResult(state=OURS, health=health, lock=read_lock(),
                       url=str(health.get("url") or app_config.base_url()),
                       pid=int(health.get("pid") or 0),
                       detail=f"すでに起動しています(PID {health.get('pid')}、"
                              f"{int(float(health.get('uptime_sec') or 0))} 秒前から)")


def refusal_text(result: GuardResult) -> str:
    """断るときに出す言葉。**次に何をすればよいかまで書きます。**"""
    if result.state == OURS:
        screen = (result.health.get("screen") or {})
        if screen.get("busy"):
            where = "すでに画面が開いています。その画面をお使いください。"
        else:
            where = ("画面は閉じられています。同じURLをブラウザで開くと"
                     "使えます:\n  " + result.url)
        return ("このアプリはすでに起動しています。\n"
                "2つ起動すると、どちらの操作が効いているのか分からなく"
                "なるため、新しく起動することはお断りしています。\n\n" + where)
    if result.state == FOREIGN:
        return (result.detail + "\n\n"
                "config/app.json の server.port を空いている番号へ変えてから"
                "起動し直してください。")
    return (result.detail + "\n\n"
            "前回の終了に失敗している可能性があります。stop.bat を実行してから"
            "起動し直してください。")


# ------------------------------------------------------------------
# 止める側から使うもの
# ------------------------------------------------------------------
def request_shutdown(port: int, token: str, *, stop_key: str = "",
                     force: bool = False,
                     timeout: float = 5.0) -> tuple[int, dict]:
    """正常終了を頼む。**返事をそのまま返します**(実行中なら 409)。

    `token` は経路を通るため、`stop_key` は**止めてよい相手かを示すため**に
    要ります(画面を持たない `stop.bat` のための合図)。
    """
    url = f"http://{app_config.host()}:{port}/api/shutdown"
    request = urllib.request.Request(
        url, data=json.dumps({"force": force,
                              "stop_key": stop_key}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Tool-Token": token,
                 "X-Stop-Key": stop_key},
        method="POST")
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


if __name__ == "__main__":        # python launch_guard.py で状態だけ見られます
    found = check_existing()
    print(f"状態: {found.state}")
    if found.detail:
        print(found.detail)
