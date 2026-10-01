"""音を鳴らすかどうかを決める (VBA ``音楽を流す`` + ``soundPlayed`` の見張り)

【鳴らすのはブラウザ、決めるのはここ】
tkinter版は winsound の PlaySound でサーバ側(=Pythonのプロセス)から
鳴らしていました。Web版でそれをやると、**`pythonw` のセッションで鳴らす**
ことになります ── 画面を見ている人に届く保証がなく、そもそも音が出る
デバイスがそのセッションに割り当たっているとは限りません。

そこで役割を分けます。

    ここ(サーバ)  … 「どの音を、いま鳴らすべきか」を決める
    ブラウザ      … 実際に鳴らす(`<audio>`)

【二重に鳴らさない】
VBA はモジュール変数 `soundPlayed As Boolean` で「もう鳴らした」を
覚えていました。同じ判断が1分ごとに真になるので、押さえないと1分おきに
鳴り続けます。ここでも同じ見張りを持ちますが、**プロセスが覚えます** ──
画面側に持たせると、タブを開き直すたびにまた鳴ります。

【自動再生の制限は、ここでは扱わない】
ブラウザは「利用者がそのページを一度も触っていない」あいだ、音を鳴らせ
ません。それはブラウザ側の事情なので `app/static/js/sound.js` が扱い、
鳴らせなかったときは画面に出します(音は補助で、文言が本体)。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from ..logging_setup import get_logger

log = get_logger("sound")

# 鳴らす音の種類。**鍵は3つだけ**で、ファイル名とは分けてある ── 名前は
# 現場で差し替えられる(設定画面)ので、コードが名前を持つと変えられない
KEY_PRINT_REMINDER = "print_reminder"
KEY_DAILY_RESULT = "daily_result"
KEY_NEGATIVE_TIME = "negative_time"
# 保存前のチェックで鳴らす2つ(`logic/save_checks.py`)。VBA も
# `Number_Count` / `CheckAndColorCells_NotExist` の中で鳴らしていた
KEY_PACK_OVER = "pack_over"
KEY_UNKNOWN_STOP = "unknown_stop"

SOUND_KEYS: tuple[str, ...] = (
    KEY_PRINT_REMINDER, KEY_DAILY_RESULT, KEY_NEGATIVE_TIME,
    KEY_PACK_OVER, KEY_UNKNOWN_STOP)


@dataclass(frozen=True)
class SoundSpec:
    """1つの音の決めごと。**文言も持つ。**

    鳴らせなかったとき(自動再生の制限・ファイルが無い)に画面へ出すのは
    この文言です ── 音は補助で、伝えたいことは文字のほうにあります。
    """

    key: str
    label: str          # 設定画面に出す名前
    default_file: str   # VBA と同じ既定のファイル名
    message: str        # 鳴らせないときに画面へ出す文言


# VBA `音楽を流す` が鳴らしていた3つ。既定のファイル名もそのまま
SOUNDS: tuple[SoundSpec, ...] = (
    # 鍵と既定のファイル名は VBA のまま(現場にその .wav がある)。**文言だけ
    # 直しました** ── 紙は任意になったので、催促しているのは印刷ではなく
    # 「直が終わる前に日報を確かめること」です
    SoundSpec(KEY_PRINT_REMINDER, "直の終わりの催促",
              "印刷忘れにご注意.wav", "直が終わります。日報の入力をお確かめください"),
    SoundSpec(KEY_DAILY_RESULT, "本日の梱包結果",
              "本日の梱包結果なのだ.wav", "本日の梱包結果を確認してください"),
    SoundSpec(KEY_NEGATIVE_TIME, "作業時間がマイナス",
              "作業時間がマイナスです.wav", "作業時間がマイナスです"),
    SoundSpec(KEY_PACK_OVER, "梱包数が検入枚数より多い",
              "梱包数が多い.wav", "梱包数が検入枚数を超えています"),
    SoundSpec(KEY_UNKNOWN_STOP, "停止内訳にない記号",
              "登録に存在しない停止.wav", "停止内訳にない記号があります"),
)

_BY_KEY = {s.key: s for s in SOUNDS}


def spec(key: str) -> Optional[SoundSpec]:
    return _BY_KEY.get(key)


class SoundGate:
    """「もう鳴らした」を覚える見張り (VBA ``soundPlayed``)。

    同じ判断(直の終わりが近い等)は1分ごとに真になり続けるので、
    押さえないと鳴り続けます。**直が変われば忘れます** ── 鍵に直を
    混ぜるのは呼び出し側の仕事で、ここは渡された鍵をそのまま覚えます。
    """

    def __init__(self) -> None:
        self._played: set[str] = set()
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        """まだ鳴らしていなければ True(そして鳴らしたことにする)。"""
        with self._lock:
            if key in self._played:
                return False
            self._played.add(key)
        log.info("音を鳴らします: %s", key)
        return True

    def forget(self, key: Optional[str] = None) -> None:
        """覚えを捨てる。直が変わったときと、テストで使う。"""
        with self._lock:
            if key is None:
                self._played.clear()
            else:
                self._played.discard(key)

    def already(self, key: str) -> bool:
        with self._lock:
            return key in self._played


# プロセスに1つ。**画面側に持たせない** ── タブを開き直すたびに鳴る
_gate = SoundGate()


def gate() -> SoundGate:
    return _gate


def reset() -> None:
    """テスト用。"""
    _gate.forget()
