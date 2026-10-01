r"""作業日(E1)の書き方を、読める形にそろえる

【なぜ要るのか】
日報の作業日は `2026年8月23日` と書かれているのが前提です(読み手は
`nippou.logic.shift.parse_business_date` で、この形しか受けません)。
ところが古いブックには**別の書き方**が入っています。

    2025/7/15      スラッシュ(実物: 2025.07.16 のブック)
    2025-07-15     ハイフン
    45853          Excel が日付として持っている数(表示だけ 2025/7/15)

どれも**同じ日付**なので、読めない形を読める形に直してから渡します ──
「書いてあるのに読めません」で止めるより、同じ日付として読むほうが
利用者の見ているものに合います。**日付そのものは変えません。**

【シート名は最後の手】
E1 がどうしても読めないときだけ、シート名の頭(`2025年7月15日_機側`)を
使います。シート名と E1 が食い違うブックがあるので、**E1 が読めるなら
E1 が優先**です。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

#: すでに読める形(`parse_business_date` と同じ見方)
BUSINESS = re.compile(r"(\d+)年(\d+)月(\d+)日")

#: スラッシュ・ハイフン・ピリオド区切り(2025/7/15, 2025-07-15, 2025.7.15)
_SEPARATED = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")

#: 8桁(20250715)
_PACKED = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

#: Excel が日付を数で持つときの起点。**1900年の閏日の分で2日ずらす**のが
#: Excel の決まり(1900年2月29日という存在しない日を数えている)
_EPOCH = date(1899, 12, 30)

#: 数として読むのは、この範囲だけ。枚数や時間を日付と取り違えないため
_SERIAL_MIN = 20000        # 1954-10-03
_SERIAL_MAX = 80000        # 2119-01-29


def to_business_date(text: str) -> str:
    """読める形(`2025年7月15日`)にして返す。直せなければ空文字。"""
    value = str(text or "").strip()
    if not value:
        return ""
    if BUSINESS.search(value):
        return value                              # すでに読める

    found = _SEPARATED.match(value) or _PACKED.match(value)
    if found:
        year, month, day = (int(part) for part in found.groups())
        return _format(year, month, day)

    try:
        number = float(value)
    except ValueError:
        return ""
    if number.is_integer() and _SERIAL_MIN <= number <= _SERIAL_MAX:
        moment = _EPOCH + timedelta(days=int(number))
        return _format(moment.year, moment.month, moment.day)
    return ""


def _format(year: int, month: int, day: int) -> str:
    try:
        date(year, month, day)
    except ValueError:                            # 2025年13月40日 のような値
        return ""
    return f"{year}年{month}月{day}日"


def from_sheet_name(sheet_name: str) -> str:
    """シート名の頭(`2025年7月15日_機側`)から。読めなければ空文字。"""
    return to_business_date(str(sheet_name or "").split("_")[0])


def fix_rows(rows: dict, sheet_name: str = "") -> tuple[dict, str]:
    """作業日(E1)を読める形に直した行を返す。

    戻りは (行, 直した言葉)。直していなければ2つめは空文字です ──
    **黙って直さない**で、画面とログに「こう読みました」と出すため。
    """
    head = rows.get(1, {})
    written = str(head.get("E", "") or "").strip()
    if BUSINESS.search(written):
        return rows, ""

    fixed = to_business_date(written) or from_sheet_name(sheet_name)
    if not fixed:
        return rows, ""

    rows = dict(rows)
    rows[1] = dict(head, E=fixed)
    source = "シート名" if not to_business_date(written) else "作業日(E1)"
    return rows, f"作業日を {written or '(空)'} → {fixed} として読みました({source})"
