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

このモジュールはアプリ本体に依存しません。起動の判断だけを持つので、
サーバが立たない状況でも動きます。
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
                    app_root=str(app_config.APP_ROOT))


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
