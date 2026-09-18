"""多重起動の判定(基盤仕様書 2.4)

【何を防ぐのか】
バックエンドが2つ上がると、同じ設定ファイルを2つのプロセスが書き、片方の
変換がもう片方の出力先を上書きし、どちらのログを見ればよいかも分からなく
なります。**同じアプリは1つだけ**にします。

【どう判定するのか】
目印のファイルだけでは足りません ── 強制終了されたあとのPIDファイルは
残ったままで、「起動しているのに起動していない」ことになります。なので
**実際に応答を確かめます**:

    1. ポートへ繋がるか                繋がらなければ空き
    2. 繋がったら起動確認APIを叩く      返事の `app_id` を見る
    3. `app_id` が同じなら             すでに起動している(断る)
       違う/返事にならなければ         別のアプリがポートを使っている(直せない)

目印のファイル(`runtime/instance.json`)は、PID と停止用の合図を渡すために
残します ── 判定の主役ではなく、停止のための控えです。
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app import appinfo

#: 判定の結果
FREE = "free"            # 空いている。起動してよい
OURS = "ours"            # このアプリがすでに起動している
FOREIGN = "foreign"      # 別のものがポートを使っている
UNKNOWN = "unknown"      # 繋がるが、何なのか答えない


@dataclass
class Existing:
    state: str = FREE
    url: str = ""
    pid: int = 0
    detail: str = ""
    info: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.state == OURS

    @property
    def blocked(self) -> bool:
        """起動してはいけない状態(すでにある / ポートが塞がっている)。"""
        return self.state != FREE


def _port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _ask_health(timeout: float = 2.0) -> dict:
    url = appinfo.base_url() + "api/health"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def probe(timeout: float = 2.0) -> Existing:
    """すでに起動しているか。**応答で確かめます**(目印だけで決めません)。"""
    host, port = appinfo.host(), appinfo.port()
    if not _port_open(host, port):
        clear_instance()                          # 残っていた目印は嘘なので消す
        return Existing(state=FREE)
    try:
        found = _ask_health(timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return Existing(state=UNKNOWN, url=appinfo.base_url(),
                        detail=f"ポート {port} は使われていますが、"
                               f"何のアプリか答えません({exc})")
    if str(found.get("app_id")) != appinfo.app_id():
        return Existing(state=FOREIGN, url=appinfo.base_url(),
                        info=found,
                        detail=f"ポート {port} は別のアプリが使っています"
                               f"({found.get('name') or found.get('app_id')})")
    return Existing(state=OURS, url=str(found.get("url") or appinfo.base_url()),
                    pid=int(found.get("pid") or 0), info=found,
                    detail=f"すでに起動しています"
                           f"(PID {found.get('pid')}、"
                           f"{int(found.get('uptime') or 0)} 秒前から)")


def refusal_text(existing: Existing) -> str:
    """断るときに出す言葉。**次に何をすればよいかまで書きます。**"""
    if existing.state == OURS:
        screen = (existing.info.get("screen") or {})
        if screen.get("busy"):
            where = "すでに画面が開いています。その画面をお使いください。"
        else:
            where = ("画面は閉じられています。同じURLをブラウザで開くと"
                     "使えます:\n  " + existing.url)
        return ("このアプリはすでに起動しています。\n"
                "2つ起動すると、どちらの操作が効いているのか分からなく"
                "なるため、新しく起動することはお断りしています。\n\n"
                + where)
    if existing.state == FOREIGN:
        return (existing.detail + "\n\n"
                "config/app.json の server.port を空いている番号へ変えてから"
                "起動し直してください。")
    return (existing.detail + "\n\n"
            "前回の終了に失敗している可能性があります。stop.bat を実行してから"
            "起動し直してください。")


# ------------------------------------------------------------------
# 目印(停止のための控え)
# ------------------------------------------------------------------
def write_instance(info: dict) -> Path:
    appinfo.ensure_local_dirs()
    path = appinfo.instance_path()
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def read_instance() -> dict:
    try:
        found = json.loads(appinfo.instance_path().read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def clear_instance() -> None:
    try:
        appinfo.instance_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


if __name__ == "__main__":        # python launch_guard.py で状態だけ見られます
    found = probe()
    print(f"状態: {found.state}")
    if found.detail:
        print(found.detail)
