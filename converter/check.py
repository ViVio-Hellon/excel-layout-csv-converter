"""読んだ数と、シート自身が持っている合計を突き合わせる

【なぜ要るのか】
このツールはシートの**座標**(どの列がどの欄か)を決め打ちで読みます。
古いブックはテンプレートが年によって違うことがあり、**列が1つずれていても
黙って通ってしまう** ── 出てきたCSVを見ても、ずれたことには気づけません。

さいわい、シートは自分で合計を持っています:

    AA55 / AB55   1直の 枚数・重量
    AA103 / AB103 2直
    AA151 / AB151 3直
    AA3 / AB3     その日の合計
    T4 / V4 / X4  停止の3分類(管理ロス / 突発 / ハンドリング)

**読んだ明細を足した数が、これと合うかを見ます。** 合えば座標は合って
いたと言えますし、合わなければ**どの直のどの数がいくつ違うか**まで
言えます。これが「座標の把握」の実体です。

【この層の約束】
ここは**純粋**。ファイルもDBも触らず、渡された数を比べるだけ。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 突き合わせの許し。重量は小数第1位まで持っているので、丸めのぶんだけ見る
COUNT_TOLERANCE = 0.01
WEIGHT_TOLERANCE = 0.05
MINUTE_TOLERANCE = 0.01


def to_number(text) -> float:
    """数として読む。読めなければ 0。"""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Line:
    """突き合わせ1件。**何を比べて、いくつ違ったか**まで持つ。"""

    what: str            # 「1直 枚数」
    read: float          # 明細を足した数
    sheet: float         # シートが持っている数
    tolerance: float = COUNT_TOLERANCE

    @property
    def ok(self) -> bool:
        return abs(self.read - self.sheet) <= self.tolerance

    @property
    def gap(self) -> float:
        return self.read - self.sheet

    def as_dict(self) -> dict:
        return {"what": self.what, "read": self.read, "sheet": self.sheet,
                "ok": self.ok, "gap": self.gap}

    def text(self) -> str:
        if self.ok:
            return f"{self.what}: {self.read:g} ○"
        return (f"{self.what}: 読んだ数 {self.read:g} / シート {self.sheet:g} "
                f"(差 {self.gap:+g}) ×")


@dataclass
class Report:
    """1ファイルぶんの突き合わせ。"""

    name: str = ""
    lines: list[Line] = field(default_factory=list)
    #: シートが合計を持っていなかった(古い形など)。**×とは別**
    no_totals: bool = False

    @property
    def ok(self) -> bool:
        """**合計が無いときは ○ にしません。** 確かめていないので。"""
        if self.no_totals:
            return False
        return all(line.ok for line in self.lines)

    @property
    def bad(self) -> list[Line]:
        return [line for line in self.lines if not line.ok]

    def summary(self) -> str:
        if self.no_totals:
            return "シートに合計が入っていないので、突き合わせられません"
        if self.ok:
            return f"{len(self.lines)}項目すべて一致"
        return (f"{len(self.bad)}/{len(self.lines)}項目がちがいます ── "
                + " / ".join(line.text() for line in self.bad[:4]))

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "no_totals": self.no_totals,
                "summary": self.summary(),
                "lines": [line.as_dict() for line in self.lines]}


def build(name: str, *, read_by_shift: dict, read_stops: float,
          sheet_by_shift: dict, sheet_day: tuple, sheet_stops: dict) -> Report:
    """突き合わせを組む。

    `read_by_shift`  … 直 → (枚数, 重量)。**明細を足したもの**
    `sheet_by_shift` … 直 → (枚数, 重量)。**シートの合計欄**
    `sheet_day`      … (枚数, 重量)。その日の合計
    `sheet_stops`    … 分類 → 分。3つ足したものと比べる
    """
    report = Report(name=name)
    totals_present = (any(c or w for c, w in sheet_by_shift.values())
                      or any(sheet_day) or any(sheet_stops.values()))
    if not totals_present:
        report.no_totals = True
        return report

    for shift in sorted(set(read_by_shift) | set(sheet_by_shift)):
        read_c, read_w = read_by_shift.get(shift, (0.0, 0.0))
        sheet_c, sheet_w = sheet_by_shift.get(shift, (0.0, 0.0))
        report.lines.append(Line(f"{shift} 枚数", read_c, sheet_c,
                                 COUNT_TOLERANCE))
        report.lines.append(Line(f"{shift} 重量", read_w, sheet_w,
                                 WEIGHT_TOLERANCE))

    read_day_c = sum(c for c, _w in read_by_shift.values())
    read_day_w = sum(w for _c, w in read_by_shift.values())
    report.lines.append(Line("日計 枚数", read_day_c, sheet_day[0],
                             COUNT_TOLERANCE))
    report.lines.append(Line("日計 重量", read_day_w, sheet_day[1],
                             WEIGHT_TOLERANCE))
    report.lines.append(Line("停止 合計(分)", read_stops,
                             sum(sheet_stops.values()), MINUTE_TOLERANCE))
    return report
