"""Shift (直) determination and print-reminder timer math.

Ports ``TimeCheck`` / ``TodayCheck`` / ``PrintCheck`` / ``GetCurrentShiftEndTime``
/ ``GetCurrentShiftStartTime`` / ``GetMinutesUntilShiftEnd`` /
``IsNear15MinutesBeforeShiftEnd`` / ``CheckCurrentShiftPrintForgotten`` /
``ShouldExecuteAutoPrint`` (standard module, ~line 1827-2610).

The VBA globals ``Start1``/``End1``/``Start2``/``End2``/``Start3``/``End3``/
``Start昼``/``End昼`` were loaded once at startup from an Access "時間用"
table (``Set_Shift`` / ``shiftTime``); here they live in :class:`ShiftTimes`,
populated from the local ``shift_config`` SQLite table that the Access
importer keeps in sync (see ``db/repository.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

DAY_SHIFT = "日勤"
SHIFT_1 = "1直"
SHIFT_2 = "2直"
SHIFT_3 = "3直"

#: 直の名前と、その並び。**紙に出る順**(五十音でも時刻順でもない)。
#:
#: **4つを並べるところは、ここから取ります。** 同じ4つを別々に書くと、
#: 直が増えたときに直し漏れた側だけが黙って1つ落とします ── 落ちた直は
#: 「選べない」「グラフに出ない」という形で、ずっと後になって出ます。
#: 並びそのものに意味がある表(`services/load_factor.SHIFT_ORDER` は
#: 負荷の引き継ぎ順なので**わざと違う並び**)は、そちらで持ちます。
SHIFT_NAMES: tuple[str, ...] = (SHIFT_1, SHIFT_2, SHIFT_3, DAY_SHIFT)

_BUSINESS_DATE_RE = re.compile(r"(\d+)年(\d+)月(\d+)日")


def parse_business_date(text: str) -> Optional[date]:
    """Inverse of ``services.nippou_service.format_business_date``: parses
    VBA's ``Format$(Date, "yyyy""年""m""月""d""日"")`` style string
    (``report_date`` as stored -- month/day are *not* zero-padded) back
    into a :class:`date`.

    Returns ``None`` for anything that doesn't match. Used by aggregation/
    reporting queries that need to filter or sort stored ``report_date``
    strings by calendar date -- plain string comparison sorts these wrong
    (e.g. "...8月30日" < "...8月3日" lexicographically) since the format
    isn't zero-padded.
    """
    match = _BUSINESS_DATE_RE.fullmatch((text or "").strip())
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


#: 画面で見る直と、時間マスタ(`shift_config`)での鍵。
#:
#: **日勤は数えません。** 1直・2直の代わりに置く直なので、入っていない
#: ことのほうが普通です ── 毎回「日勤の時間がありません」と出しても、
#: 読まずに閉じる癖がつくだけです。
MASTER_KEYS: tuple[tuple[str, str], ...] = (("1", "1直"), ("2", "2直"),
                                            ("3", "3直"))


def missing_shift_times(raw: dict[str, tuple[str, str]]) -> list[str]:
    """**マスタから読めていない直**の名前。空なら3直ぶんとも本物。

    片側だけ入っているもの(`("15:00", "")`)も読めていない扱いにします
    ── 読む側(`build_shift_calculator` / `shift_check.bounds_of`)が
    どちらも「片側でも欠ければ控えに落とす」ので、それに合わせます。
    """
    missing = []
    for key, name in MASTER_KEYS:
        found = (raw or {}).get(key)
        if not (found and found[0] and found[1]):
            missing.append(name)
    return missing


def shift_times_note(raw: dict[str, tuple[str, str]]) -> str:
    """控えで動いているなら、そう言う文。本物なら空。

    【なぜ黙ってはいけないか】
    控えは「時間マスタを取り込む前でも動かす」ためのものです。ところが
    **控えで動いていることは画面のどこにも出ていませんでした。**
    現場は 07:00-15:00-22:50、控えが 08:00-17:00-22:00 だった頃は、
    取り込めていない端末だけが2時間ずれたまま動き、催促も自動確定も
    最終時間チェックも全部ずれるのに、誰も気づけません。

    値を合わせても同じことです ── **合っている保証がどこにも無い**のが
    問題なので、出どころのほうを画面に出します。
    """
    missing = missing_shift_times(raw)
    if not missing:
        return ""
    return (f"直の時間をマスタから読めていません({'・'.join(missing)})。"
            "いまは控えの時刻で動いています ── "
            "設定・管理者の「時間マスタを取り込む」を押してください。")


@dataclass(frozen=True)
class ShiftTimes:
    """Shift start/end boundaries, "HH:MM" 24h strings.

    Sensible factory defaults are supplied so the app is usable before the
    first Access sync; a real deployment should populate ``shift_config``
    from the "時間用" master table, exactly as ``Set_Shift`` did.
    """

    #: 現場の直は **07:00 - 15:00 - 22:50 - 07:00**。
    #:
    #: ここは「マスタを取り込む前でも動かす」ための控えですが、控えが
    #: 2時間ずれていると、**取り込めていない端末だけが黙って別の時刻で
    #: 動きます** ── 催促も自動確定も最終時間チェックも全部ずれるのに、
    #: 画面には何も出ません。控えるなら本物と同じ値を控えます。
    #:
    #: 本番は `shift_config`(伝送用ファイルの「時間用」)が上書きします。
    start1: str = "07:00"
    end1: str = "15:00"
    start2: str = "15:00"
    end2: str = "22:50"
    start3: str = "22:50"
    end3: str = "07:00"
    #: 日勤は1直・2直の代わりに置く直。**現場の値は未確認**なので、
    #: 1直の始まりに合わせて8時間ぶんを控えにしてあります
    start_day: str = "07:00"
    end_day: str = "17:00"

    def bounds(self, shift: str) -> tuple[time, time]:
        mapping = {
            SHIFT_1: (self.start1, self.end1),
            SHIFT_2: (self.start2, self.end2),
            SHIFT_3: (self.start3, self.end3),
            DAY_SHIFT: (self.start_day, self.end_day),
        }
        s, e = mapping[shift]
        return _parse_hhmm(s), _parse_hhmm(e)


DEFAULT_SHIFT_TIMES = ShiftTimes()


class ShiftCalculator:
    """Stateless helper bundling the shift-boundary math. All methods take
    the "now" instant explicitly (rather than reading the system clock)
    so tests are deterministic."""

    def __init__(self, times: ShiftTimes = DEFAULT_SHIFT_TIMES) -> None:
        self.times = times

    def time_check(self, now: datetime, force_day_shift: bool = False) -> str:
        """Port of ``TimeCheck``. ``force_day_shift`` replaces the VBA
        check against ``UFdaily.TOT/BALA/MARU`` -- pass True when the
        currently selected line is one of :data:`nippou.constants.DAY_SHIFT_ALWAYS_LINES`."""
        if force_day_shift:
            return DAY_SHIFT

        t = now.time()
        start1, end1 = self.times.bounds(SHIFT_1)
        start2, end2 = self.times.bounds(SHIFT_2)
        start3, end3 = self.times.bounds(SHIFT_3)

        if start1 < t < end1:
            return SHIFT_1
        if start2 <= t < end2:
            return SHIFT_2
        if start3 <= t <= time(23, 59):
            return SHIFT_3
        if time(0, 0) <= t <= end3:
            return SHIFT_3
        return SHIFT_3  # VBA's final catch-all ("23:59 timing" fallback)

    def today_check(self, now: datetime, force_day_shift: bool = False) -> date:
        """Port of ``TodayCheck``: the *business* date this instant
        belongs to. Only the early-morning tail of the night shift (3直,
        from 00:00 to End3) is attributed to the previous calendar day."""
        if force_day_shift:
            return now.date()

        t = now.time()
        start1, end1 = self.times.bounds(SHIFT_1)
        start2, end2 = self.times.bounds(SHIFT_2)
        start3, end3 = self.times.bounds(SHIFT_3)

        if start1 < t < end1:
            return now.date()
        if start2 <= t < end2:
            return now.date()
        if start3 <= t <= time(23, 59):
            return now.date()
        if time(0, 0) <= t <= end3:
            return now.date() - timedelta(days=1)
        return now.date()

    def current_shift_start_time(self, shift: str) -> Optional[time]:
        if shift not in SHIFT_NAMES:
            return None
        return self.times.bounds(shift)[0]

    def current_shift_end_time(self, shift: str) -> Optional[time]:
        if shift not in SHIFT_NAMES:
            return None
        return self.times.bounds(shift)[1]

    def minutes_until_shift_end(self, now: datetime, force_day_shift: bool = False) -> int:
        """Port of ``GetMinutesUntilShiftEnd``. Returns -1 if the shift
        can't be determined (mirrors the VBA sentinel)."""
        shift = self.time_check(now, force_day_shift)
        end_t = self.current_shift_end_time(shift)
        if end_t is None:
            return -1

        end_dt = datetime.combine(now.date(), end_t)
        if shift == SHIFT_3:
            # 3-shift's end time is the following morning once we're past
            # noon (i.e. we're in the evening portion of the shift).
            if now.time() >= time(12, 0):
                end_dt += timedelta(days=1)
        delta = end_dt - now
        return int(delta.total_seconds() // 60)

    def is_near_shift_end(
        self, now: datetime, force_day_shift: bool = False, warn_minutes: int = 15
    ) -> bool:
        """Port of ``IsNear15MinutesBeforeShiftEnd``."""
        minutes_left = self.minutes_until_shift_end(now, force_day_shift)
        return 0 <= minutes_left <= warn_minutes

    def shift_end_at(self, shift: str,
                     business_date: date) -> Optional[datetime]:
        """**名指しした直**の終わり(日付つき)。分からなければ None。

        `minutes_until_shift_end` は「いま時計が指している直」の終わりしか
        返しません。15:05 に「14:00 に始めた1直はいつ終わったのか」を
        訊きたい場面があるので(`logic/shift_anchor.py`)、直を名指しで
        渡せる形も要ります。

        **日をまたぐ直は、終わりが翌日**です。3直だけを特別扱いせず、
        「終わりが始まりより前なら翌日」で決めます ── マスタの時刻が
        変わっても、ここを直さずに済みます。報告日は `TodayCheck` と
        同じで**始めた日**のほうです。
        """
        try:
            start_t, end_t = self.times.bounds(shift)
        except (KeyError, ValueError):
            return None
        end_dt = datetime.combine(business_date, end_t)
        if end_t <= start_t:
            end_dt += timedelta(days=1)
        return end_dt


class ShiftCloseChecker:
    """直の終わりの催促と、自動で締める判断。

    VBA の ``CheckCurrentShiftPrintForgotten`` / ``ShouldExecuteAutoPrint``
    の移植ですが、**追いかける相手が「印刷」から「確定」に変わりました。**
    VBA は残り5分で印刷処理まで走らせ、印刷したかどうかで催促を止めて
    いました。こちらは紙を**作業者が欲しいときだけ**出すものにしたので、
    直の終わりまでに済ませたいのは 保存・集計の確定 のほうです。

    ``is_shift_closed`` と ``has_pending_data`` は差し込みの関数です
    (DBや画面が無くても試せるように)。それぞれ VBA の
    ``IsTodayShiftPrintExecuted``(いまは `print_status` の表 =
    「その直を締めたか」)と ``HasDataInPrintRange`` に当たります。
    """

    def __init__(
        self,
        calc: ShiftCalculator,
        is_shift_closed,
        warn_minutes: int = 15,
        auto_close_minutes: int = 5,
    ) -> None:
        self.calc = calc
        self.is_shift_closed = is_shift_closed
        self.warn_minutes = warn_minutes
        self.auto_close_minutes = auto_close_minutes

    def close_forgotten(self, now: datetime, force_day_shift: bool = False) -> bool:
        """催促を出すころか(残り `warn_minutes` 以内で、まだ締めていない)。"""
        shift = self.calc.time_check(now, force_day_shift)
        business_date = self.calc.today_check(now, force_day_shift)
        if self.is_shift_closed(shift, business_date):
            return False
        end_t = self.calc.current_shift_end_time(shift)
        if end_t is None:
            return False
        return self.calc.is_near_shift_end(now, force_day_shift, self.warn_minutes)

    def should_auto_close(
        self,
        now: datetime,
        force_day_shift: bool,
        recall_mode: bool,
        already_closed: bool,
        has_pending_data: bool,
    ) -> bool:
        if recall_mode or already_closed:
            return False
        shift = self.calc.time_check(now, force_day_shift)
        business_date = self.calc.today_check(now, force_day_shift)
        if self.is_shift_closed(shift, business_date):
            return False
        minutes_left = self.calc.minutes_until_shift_end(now, force_day_shift)
        if minutes_left > self.auto_close_minutes:
            return False
        return has_pending_data

    def realtime_warning_message(self, now: datetime, force_day_shift: bool = False) -> str:
        """催促の文言。**紙のことは言いません**(紙は任意なので)。"""
        shift = self.calc.time_check(now, force_day_shift)
        minutes_left = self.calc.minutes_until_shift_end(now, force_day_shift)
        tail = "日報の入力をお確かめください！"
        if minutes_left <= 0:
            return f"【{shift} 終了時刻超過】{tail}"
        if minutes_left == 1:
            return f"【{shift} 終了1分前】{tail}"
        return f"【{shift} 終了{minutes_left}分前】{tail}"
