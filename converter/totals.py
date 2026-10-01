"""シート自身が持っている合計を、**「無い」と「0」を区別して**拾う

【なぜ別に用意したのか】
日報管理ツールの `nippou_sheet.read_totals` は、合計欄を `float` で返します
(空欄は `0.0`)。検算の相手としてはこれで足りていたのですが、**空欄と0が
同じ顔になります**。

実物でこれが問題になりました。2025年のブックは

    AA55 / AA103 / AA151   16 / 34 / 12   ← 直合計は入っている
    AA3                    28             ← 日計だけ古い(式が計算し直されていない)
    T4 / V4 / X4           (空)           ← 停止の合計は値を持っていない

という状態でした。空欄を0とみなすと「停止 合計(分): 読んだ数 870 /
シート 0 ×」と出ます ── **読み取りは合っているのに、ずれていると
言ってしまう**わけで、これはいちばんやってはいけない嘘です。

なので**ここは `None`(シートが持っていない)を返します**。比べる側
(`check.build`)が、持っていないものは比べずに「比べていない」と言います。

【座標は借ります】
直の切れ目と合計行は日報管理ツールの `SHIFT_BANDS` をそのまま使います
(こちらで書き写すと、片方だけ直したときにずれます)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 停止の3分類。`nippou_sheet.read_totals` と同じ欄・同じ呼び名
STOP_CELLS: tuple[tuple[str, str], ...] = (
    ("管理ロス停止", "T"),
    ("突発停止", "V"),
    ("ハンドリング停止", "X"),
)

#: 日計が載っている行(枚数・重量)
DAY_ROW, DAY_COUNT, DAY_WEIGHT = 3, "AA", "AB"

#: 停止の合計が載っている行
STOP_ROW = 4


@dataclass
class SheetTotals:
    """シートが持っている合計。**値が `None` なら「持っていない」。**"""

    by_shift: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    day: tuple[float | None, float | None] = (None, None)
    stops: dict[str, float | None] = field(default_factory=dict)

    @property
    def nothing(self) -> bool:
        """1つも持っていない(古い形のブックなど)。"""
        return not any(
            value is not None
            for value in ([c for c, _w in self.by_shift.values()]
                          + [w for _c, w in self.by_shift.values()]
                          + list(self.day) + list(self.stops.values())))


def number_or_none(text) -> float | None:
    """数として読む。**空欄や数でないものは `None`**(0ではありません)。

    式の結果が保存されていないセルは、空欄として入ってきます。
    `#VALUE!` のような文字も「比べられない」なので `None` にします。
    """
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


#: 停止の「振り分け後」── 内訳名と時間の対(作業停止内訳-1/2/3)
#:
#: シートは停止を2か所に持っています。**AJ列から右は「振り分け前」**で
#: (`AJ4` にそう書いてあります)、記号のまま先に書き込む欄です。左の
#: S〜X がそれを停止名に振り分けたもので、**シート自身の合計
#: (T4/V4/X4 = 管理ﾛｽ/段取り・突発/ﾊﾝﾄﾞﾘﾝｸﾞ)もこちら側**を数えています。
#:
#: だから検算もこちら側で数えます ── 振り分け前は作業用の欄で、
#: 書き損じ(用途名が入っているなど)もあり得るので数えません。
STOP_PAIRS: tuple[tuple[str, str], ...] = (("S", "T"), ("U", "V"), ("W", "X"))

#: 明細の範囲(見出しの下から、3直の終わりまで)
FIRST_ROW, LAST_ROW = 8, 151


def stop_minutes(rows: dict[int, dict[str, str]]) -> float:
    """**振り分け後**の停止時間の合計(分)。シートの T4/V4/X4 と比べる相手。"""
    found = 0.0
    for number in range(FIRST_ROW, LAST_ROW + 1):
        values = rows.get(number, {})
        for _name, minutes in STOP_PAIRS:
            value = number_or_none(values.get(minutes, ""))
            if value is not None:
                found += value
    return found


def read(rows: dict[int, dict[str, str]]) -> SheetTotals:
    """合計欄を拾う。**シートの中身は変えません。**"""
    from nippou.logic.nippou_sheet import SHIFT_BANDS

    day = rows.get(DAY_ROW, {})
    stop = rows.get(STOP_ROW, {})
    return SheetTotals(
        by_shift={shift: (number_or_none(rows.get(total, {}).get(DAY_COUNT, "")),
                          number_or_none(rows.get(total, {}).get(DAY_WEIGHT, "")))
                  for shift, _start, _end, total in SHIFT_BANDS},
        day=(number_or_none(day.get(DAY_COUNT, "")),
             number_or_none(day.get(DAY_WEIGHT, ""))),
        stops={name: number_or_none(stop.get(column, ""))
               for name, column in STOP_CELLS})
