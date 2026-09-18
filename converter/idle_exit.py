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
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import app_config
from .logging_utils import get_logger

log = get_logger("idle_exit")

#: 画面が心拍を送る間隔(ミリ秒)。**出どころはここ1つ** ── 画面と
#: サーバで別に決めると、片方を直しただけで自動終了が誤ります
HEARTBEAT_MS = 3000

#: 閉じた合図を受けてから終わるまでの猶予(秒)
GRACE_SEC = 20

#: 見張る間隔(秒)
CHECK_SEC = 5


class IdleExit:
    """心拍が途切れたら止める見張り。

    `busy` が真のあいだは終わりません(変換中)。`stop` は止めるときに
    呼ぶもので、理由を1つ受け取ります。
    """

    def __init__(self, *, busy: Callable[[], bool],
                 stop: Callable[[str], None],
                 minutes: Optional[int] = None) -> None:
        self._busy = busy
        self._stop = stop
        self._minutes = minutes
        self._lock = threading.Lock()
        self._last_beat = 0.0
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

    def mark_alive(self) -> None:
        """心拍を受けた。"""
        with self._lock:
            self._seen = True
            self._last_beat = time.time()
            self._closed_at = 0.0       # 戻ってきたので閉じた合図は取り消す

    def mark_closed(self) -> None:
        """タブが閉じられた(`sendBeacon`)。**猶予のあとで終わります。**"""
        with self._lock:
            if not self._seen:
                return
            self._closed_at = time.time()

    def status(self) -> dict:
        with self._lock:
            return {"seen": self._seen, "minutes": self.minutes,
                    "silent": int(time.time() - self._last_beat) if self._last_beat else 0,
                    "closing": bool(self._closed_at)}

    # -- 見張り ----------------------------------------------------
    def start(self) -> None:
        if self.minutes <= 0 and GRACE_SEC <= 0:
            log.info("自動終了は無効です")
            return
        self._thread = threading.Thread(target=self._watch, name="idle_exit",
                                        daemon=True)
        self._thread.start()

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
            now = time.time()
            if self._closed_at and now - self._closed_at > GRACE_SEC:
                return "画面が閉じられました"
            limit = self.minutes * 60
            if limit > 0 and self._last_beat and now - self._last_beat > limit:
                return f"画面が {self.minutes} 分ありませんでした"
            return ""
