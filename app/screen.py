"""画面の占有 ── **2枚目は開かせません**

【なぜ断るのか】
同じアプリを2枚開けると、2つの画面が同じ作業状態を奪い合います。片方で
「変換開始」、もう片方で「中止」。片方で設定を保存し、もう片方は古い設定を
出したまま。**どちらが本当か分からない**状態が生まれ、しかも利用者からは
見分けがつきません。混乱のもとなので、開くこと自体を断ります。

【3段で止めます】
1. プロセス  `launch_guard` … バックエンドを2つ起動させない
2. サーバ    ここ            … 使ってよい画面を1枚に絞る(最後の砦)
3. ブラウザ  `app.js`        … 同じ画面の複製を、開いた側が自分で断る

2だけでは複製タブ(sessionStorage ごと複製される)を見分けられず、3だけ
では別のブラウザや直接URLを打った場合に効きません。**両方あって1枚**です。

【再読込は断りません】
同じタブの再読込で断ってしまうと、一度読み込みに失敗した画面を開き直せなく
なります。タブは自分の `tab_id` を持ち歩くので、**同じ `tab_id` なら
持ち主の入れ替え**として通します。

【閉じたあとは空きます】
画面は生存確認を送り続けます(既定3秒ごと)。途切れて `screen_timeout_seconds`
を越えたら、その画面は居なくなったとみなして空けます ── ブラウザが落ちた
まま二度と開けない、という状態を作らないためです。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import appinfo


@dataclass
class Holder:
    """いま使っている画面。"""

    tab_id: str
    token: str
    load_id: str = ""
    agent: str = ""
    since: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def age(self) -> float:
        return time.time() - self.since

    def silent_for(self) -> float:
        return time.time() - self.last_seen


@dataclass
class Claim:
    """占有を求めた結果。断るときは**理由を必ず添えます。**"""

    ok: bool
    token: str = ""
    reason: str = ""
    detail: str = ""
    retry_after: int = 0


class ScreenLock:
    def __init__(self, timeout_seconds: Optional[int] = None) -> None:
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._holder: Optional[Holder] = None

    # -- 内部 -------------------------------------------------------
    @property
    def timeout(self) -> int:
        if self._timeout is not None:
            return self._timeout
        return appinfo.screen_timeout_seconds()

    def _current(self) -> Optional[Holder]:
        """**呼ぶたびに期限切れを片づけます。** 掃除役を別に持たないため。"""
        holder = self._holder
        if holder and holder.silent_for() > self.timeout:
            self._holder = None
            return None
        return holder

    # -- 外から ------------------------------------------------------
    def claim(self, tab_id: str, load_id: str = "", agent: str = "") -> Claim:
        tab_id = (tab_id or "").strip()
        if not tab_id:
            return Claim(False, reason="画面の識別子がありません",
                         detail="ブラウザの設定で保存が禁止されている可能性があります")
        with self._lock:
            holder = self._current()
            if holder and holder.tab_id != tab_id:
                return Claim(
                    False,
                    reason="この画面は開けません",
                    detail=(f"すでに別の画面で開かれています"
                            f"(開かれてから {int(holder.age())} 秒)。"
                            "2枚開くと、どちらの操作が効いているのか"
                            "分からなくなるため、開くことをお断りしています。"),
                    retry_after=self.timeout)
            token = secrets.token_urlsafe(16)
            self._holder = Holder(tab_id=tab_id, token=token, load_id=load_id,
                                  agent=agent[:120],
                                  since=holder.since if holder else time.time())
            return Claim(True, token=token)

    def beat(self, token: str) -> bool:
        """生存確認。**持ち主でなければ偽**(画面は無効表示へ切り替えます)。"""
        with self._lock:
            holder = self._current()
            if not holder or holder.token != token:
                return False
            holder.last_seen = time.time()
            return True

    def release(self, token: str) -> bool:
        with self._lock:
            holder = self._current()
            if holder and holder.token == token:
                self._holder = None
                return True
            return False

    def holds(self, token: str) -> bool:
        with self._lock:
            holder = self._current()
            return bool(holder and token and holder.token == token)

    def busy(self) -> bool:
        with self._lock:
            return self._current() is not None

    def status(self) -> dict:
        with self._lock:
            holder = self._current()
            if not holder:
                return {"busy": False}
            return {"busy": True, "age": int(holder.age()),
                    "silent": int(holder.silent_for()), "agent": holder.agent}


#: アプリで1つ。**画面は1枚**という約束そのもの
LOCK = ScreenLock()
