"""画面が居なくなったら終わる(基盤仕様書 2.8「自動終了」)

【なぜ要るのか】
このアプリに窓はありません。見えているのはブラウザのタブだけなので、
**タブを閉じたら終わったつもりになります。** ところが Python は動いた
ままで、次に起動すると多重起動の判定が「すでに起動しています」と答え
ます ── 閉じたはずのものに、起動を断られ続けることになります。

【どう決めるか】
画面が一定の間隔で心拍(`POST /api/alive`)を送ります。**途切れたら
誰も見ていない**と判断して終わります。タブを閉じたことは `sendBeacon`
で即座に伝わるので、たいていは待たずに終わります。

【間違って落とさないための3つ】
1. **猶予を置く。** 再読込や画面遷移でも心拍は一瞬途切れます。閉じた
   合図が来ても `GRACE_SEC` は待ち、そのあいだに心拍が戻れば取り消します
2. **処理中は落とさない。** 変換の途中で終わると、4本組のCSVを半分
   書いた状態が残ります
3. **1度も繋がっていなければ落とさない。** `--no-browser` で立てておく
   使い方(検証・並行運用)を巻き添えにしません

【スリープ復帰と、裏に回ったタブで誤って終わっていました】(1.11.0)
ほかのツールから挙がった報告 ──

    スリープ復帰時の時計のずれやブラウザのタブ休止により問い合わせが
    止まり、サーバーが誤って終了してしまう

このアプリも同じ作りでした。直したのは3つです。

**① 時刻はスリープを数えません**(`converter/awake_clock.py`)。
Windows はスリープ中も時計が進むので、フタを開けた瞬間
「画面が30分ありませんでした」と判定し、**起きたブラウザが心拍を送る前に
終わって**いました(時刻合わせで壁の時計が飛んだときも同じ)。

**② 裏に回ったタブは、心拍が止まっても落としません。** ブラウザは
見えていないタブのタイマーを間引きます(Chrome は隠れて5分で1分に1回、
Edge の「スリープ中のタブ」や Chrome のメモリセーバーは**止めて**
しまう)。Excel を全画面にした・窓を最小化した、だけでタブは「見えて
いない」になり、**止められたタブからは心拍が来ません。** そこで画面は
裏に回る瞬間に `hidden` を送り、以後は心拍が無くても居るものとみなします
(`HIDDEN_KEEP_SEC`)。

**③ 閉じたページからの遅れた合図で、生き返らせも落としもしません。**
タブを閉じるとブラウザは「裏に回った」と「閉じた」を続けて出し、どちらも
`sendBeacon` なので着く順は決まっていません。また `pagehide` は**凍結や
戻る/進むの控え(bfcache)でも飛びます** ── 再読込した新しいページが、
古いページの「閉じた」で落とされていました。画面は読み込むたびに別の
**ページ番号**を添え、閉じた番号から来たものは受け付けず、いま居る
ページと違う番号の「閉じた」では外しません。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from . import app_config, awake_clock
from .logging_utils import get_logger

log = get_logger("idle_exit")

#: 画面が心拍を送る間隔(ミリ秒)。**出どころはここ1つ** ── 画面と
#: サーバで別に決めると、片方を直しただけで自動終了が誤ります
HEARTBEAT_MS = 3000

#: 閉じた合図を受けてから終わるまでの猶予(秒)
GRACE_SEC = 20

#: 見張る間隔(秒)
CHECK_SEC = 5

#: 裏に回ったタブを、心拍が無くても居るものとみなす長さ
#: (秒・**起きていた時間**)。ブラウザが止めたタブからは何も来ないので、
#: ここは「待つ」ではなく**置き去りの後始末**の長さです。7日 ── 窓を
#: 最小化したまま週末を越えても落としません。閉じればその場で合図が
#: 届くので、これを待つことはまずありません
HIDDEN_KEEP_SEC = 7 * 24 * 3600.0

#: 閉じたページ番号を覚えておく長さ(秒)。遅れた合図は長くても数秒で
#: 着くので、十分に長く、しかし増え続けない長さ
GONE_KEEP_SEC = 600.0


@dataclass
class Screen:
    """開いているタブ1枚の様子。"""

    seen: float                 # 最後に合図が来た時刻(`awake_clock`)
    hidden: bool = False        # 最後の合図で「裏に回っている」と言ったか
    page: str = ""              # 読み込みごとのページ番号(古い画面は "")


class IdleExit:
    """心拍が途切れたら止める見張り。

    `busy` が真のあいだは終わりません(変換中)。`stop` は止めるときに
    呼ぶもので、理由を1つ受け取ります。`clock` は**スリープを数えない
    時計**で、試験は自分の時計を渡せます。
    """

    def __init__(self, *, busy: Callable[[], bool],
                 stop: Callable[[str], None],
                 minutes: Optional[int] = None,
                 hidden_sec: float = HIDDEN_KEEP_SEC,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._busy = busy
        self._stop = stop
        self._minutes = minutes
        self.hidden_sec = hidden_sec
        self._clock = clock or awake_clock.now
        self._lock = threading.Lock()
        #: 開いているタブ。鍵は画面の名札。名札を送ってこない古い画面は
        #: "" の1枚として数えます
        self._screens: dict[str, Screen] = {}
        #: 閉じたページ番号と、閉じた時刻。**ここから来た合図は受けません**
        self._gone: dict[str, float] = {}
        self._closed_at = 0.0
        self._seen = False              # 1度でも画面が繋がったか
        self._thread: Optional[threading.Thread] = None
        self._done = threading.Event()

    # -- 画面から --------------------------------------------------
    @property
    def minutes(self) -> int:
        if self._minutes is not None:
            return self._minutes
        return app_config.idle_exit_minutes()

    def mark_alive(self, tab: str = "", *, hidden: bool = False,
                   page: str = "") -> bool:
        """心拍を受けた。**閉じた合図も取り消します**(再読込のとき)。

        `hidden=True` は「裏に回った(見えていない)」。ブラウザが心拍を
        間引く・止める状態なので、**以後は心拍が来なくても居るものと
        みなします。**

        閉じたページ(`page`)から遅れて着いたものは受け付けません(偽)。
        """
        with self._lock:
            if page and page in self._gone:
                return False
            before = self._screens.get(tab)
            self._screens[tab] = Screen(seen=self._clock(), hidden=hidden,
                                        page=page)
            self._seen = True
            self._closed_at = 0.0       # 戻ってきたので閉じた合図は取り消す
        if before is not None and before.hidden != hidden:
            log.info("画面が%sました%s", "裏に回り" if hidden else "表に戻り",
                     f"(タブ {tab})" if tab else "")
        return True

    def mark_hidden(self, tab: str = "", *, page: str = "") -> bool:
        """裏に回った(`visibilitychange` / `freeze`)。"""
        return self.mark_alive(tab, hidden=True, page=page)

    def mark_closed(self, tab: str = "", *, page: str = "") -> None:
        """タブが閉じられた(`sendBeacon`)。**猶予のあとで終わります。**

        ほかにタブが残っていれば、その1枚を外すだけです ── 2枚目を
        閉じただけでアプリごと終わってはいけません。同じタブでも**別の
        ページ**(再読込で先に着いた新しいほう)は外しません。
        """
        with self._lock:
            if not self._seen:
                return                  # 1度も繋がっていない
            now = self._clock()
            if page:
                self._gone[page] = now
                for old in [p for p, at in self._gone.items()
                            if now - at > GONE_KEEP_SEC]:
                    del self._gone[old]
            current = self._screens.get(tab)
            if current is not None and (not page or not current.page
                                        or current.page == page):
                del self._screens[tab]
            self._prune(now)
            left = len(self._screens)
            if not left:
                self._closed_at = now
        if left:
            log.info("画面が1枚閉じました(残り %d枚)", left)
        else:
            log.info("画面が閉じられました。%d秒 待って終わります", GRACE_SEC)

    def status(self) -> dict:
        with self._lock:
            now = self._clock()
            self._prune(now)
            hidden = sum(1 for s in self._screens.values() if s.hidden)
            newest = max((s.seen for s in self._screens.values()), default=0.0)
            return {"seen": self._seen, "minutes": self.minutes,
                    "silent": int(now - newest) if newest else 0,
                    "screens": len(self._screens),
                    "hidden": hidden,
                    "closing": bool(self._closed_at)}

    # -- 見張り ----------------------------------------------------
    def start(self) -> None:
        if self.minutes <= 0 and GRACE_SEC <= 0:
            log.info("自動終了は無効です")
            return
        self._thread = threading.Thread(target=self._watch, name="idle_exit",
                                        daemon=True)
        self._thread.start()
        log.info("自動終了の見張りを始めました"
                 "(無通信 %d分 / 閉じたら %d秒 / 裏に回ったタブは %s)",
                 self.minutes, GRACE_SEC, _span(self.hidden_sec))

    def cancel(self) -> None:
        self._done.set()

    def _watch(self) -> None:
        while not self._done.wait(CHECK_SEC):
            reason = self._reason()
            if reason:
                log.info("誰も見ていないので終わります: %s", reason)
                self._stop(reason)
                return

    def _reason(self) -> str:
        with self._lock:
            if not self._seen or self._busy():
                return ""
            now = self._clock()
            if self._closed_at:
                # 最後の1枚が閉じた。**猶予のうちは待ちます**
                if now - self._closed_at > GRACE_SEC:
                    return "画面が閉じられました"
                return ""
            hidden_gone = any(s.hidden for s in self._screens.values())
            self._prune(now)
            if self._screens:
                return ""
            if self.minutes <= 0 and not hidden_gone:
                return ""               # 無通信では終わらせない設定
        if hidden_gone:
            return f"裏に回ったまま {_span(self.hidden_sec)} 音沙汰がありません"
        return f"画面が {self.minutes} 分ありませんでした"

    def _prune(self, now: float) -> None:
        """もう居ないとみなすタブを外す。**錠の中で呼びます。**

        見えているタブは `minutes` 分、裏に回ったタブは `hidden_sec`。
        裏のタブはブラウザが心拍を止めるので、短く見切ると誤って落とします。
        """
        limit = self.minutes * 60
        for tab, screen in list(self._screens.items()):
            # **0分は「無通信では終わらせない」**(設定で切ったとき)。
            # ここで 0 を見切りに使うと、見えているタブを即座に外します
            keep = self.hidden_sec if screen.hidden else limit
            if keep > 0 and now - screen.seen >= keep:
                del self._screens[tab]


def _span(sec: float) -> str:
    """長さを人の言葉で(ログと理由に出す)。"""
    if sec >= 2 * 86400:
        return f"{sec / 86400:.0f}日"
    if sec >= 3600:
        return f"{sec / 3600:.0f}時間"
    return f"{sec:.0f}秒"
