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

**数えるのは「起きていた時間」です**(`converter/awake_clock.py`)。
PC がスリープすると Windows では時計が進むので、フタを開けた瞬間に
「12秒 音沙汰なし」で占有が外れていました ── 画面もブラウザごと眠って
いただけで、誰も閉じていません。眠っていた分は差し引きます(戻った画面が
黙って取り直せるようにもしてあります ── `screen.js` の `regain`)。
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Optional

from . import app_config, awake_clock

#: 画面が自分の番号を入れて送るヘッダ。**名前の出どころはここ1つ**
HEADER = "X-Screen-Id"


@dataclass
class Holder:
    """いま使っている画面。"""

    tab_id: str
    screen_id: str
    load_id: str = ""
    agent: str = ""
    #: **スリープを数えない時計**の値(`awake_clock`)。値そのものに意味は
    #: 無く、差を取るためだけのものです。**時計は `ScreenLock` が持ちます**
    #: ── ここで既定値を作ると、試験で時計を差し替えたときに
    #: 「別の時計の値どうしを引く」ことになります
    since: float = 0.0
    last_seen: float = 0.0

    def age(self, now: float) -> float:
        return now - self.since

    def silent_for(self, now: float) -> float:
        return now - self.last_seen


@dataclass
class Claim:
    """占有を求めた結果。断るときは**理由を必ず添えます。**"""

    ok: bool
    screen_id: str = ""
    reason: str = ""
    detail: str = ""
    retry_after: int = 0


class ScreenLock:
    def __init__(self, timeout_seconds: Optional[int] = None,
                 clock=None) -> None:
        self._timeout = timeout_seconds
        #: **スリープを数えない時計。** 試験は自分の時計を渡せます
        self._clock = clock or awake_clock.now
        self._lock = threading.Lock()
        self._holder: Optional[Holder] = None

    # -- 内部 -------------------------------------------------------
    @property
    def timeout(self) -> int:
        if self._timeout is not None:
            return self._timeout
        return app_config.screen_timeout_seconds()

    def _current(self) -> Optional[Holder]:
        """**呼ぶたびに期限切れを片づけます。** 掃除役を別に持たないため。"""
        holder = self._holder
        if holder and holder.silent_for(self._clock()) > self.timeout:
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
                            f"(開かれてから {int(holder.age(self._clock()))} 秒)。"
                            "2枚開くと、どちらの操作が効いているのか"
                            "分からなくなるため、開くことをお断りしています。"),
                    retry_after=self.timeout)
            screen_id = secrets.token_urlsafe(16)
            now = self._clock()
            self._holder = Holder(tab_id=tab_id, screen_id=screen_id,
                                  load_id=load_id, agent=agent[:120],
                                  since=holder.since if holder else now,
                                  last_seen=now)
            return Claim(True, screen_id=screen_id)

    def beat(self, screen_id: str) -> bool:
        """生存確認。**持ち主でなければ偽**(画面は無効表示へ切り替えます)。"""
        with self._lock:
            holder = self._current()
            if not holder or holder.screen_id != screen_id:
                return False
            holder.last_seen = self._clock()
            return True

    def release(self, screen_id: str) -> bool:
        with self._lock:
            holder = self._current()
            if holder and holder.screen_id == screen_id:
                self._holder = None
                return True
            return False

    def holds(self, screen_id: str) -> bool:
        with self._lock:
            holder = self._current()
            return bool(holder and screen_id and holder.screen_id == screen_id)

    def busy(self) -> bool:
        with self._lock:
            return self._current() is not None

    def status(self) -> dict:
        with self._lock:
            holder = self._current()
            if not holder:
                return {"busy": False}
            now = self._clock()
            return {"busy": True, "age": int(holder.age(now)),
                    "silent": int(holder.silent_for(now)),
                    "agent": holder.agent}


#: アプリで1つ。**画面は1枚**という約束そのもの
LOCK = ScreenLock()
