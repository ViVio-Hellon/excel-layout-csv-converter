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

【シートが持っていない合計は、比べません】
実物のブックには、**合計欄が空のまま**のものがあります(式の結果が保存
されていない、など)。空欄を0とみなして比べると

    停止 合計(分): 読んだ数 870 / シート 0 ×

と出ます ── **読み取りは合っているのに、ずれていると言ってしまう**。
なので「シートが持っていないもの」は×にせず、**比べていないと言います**
(`skipped`)。確かめた項目が1つも無ければ `no_totals` です。

【シートが自分で食い違っているとき】
直合計 16/34/12 が入っているのに 日計が 28 のままのブックがありました
(合計式が計算し直されていない)。このとき責めるべきは読み取りではなく
シートです。**直計の合計と比べ**、食い違いは `notes` に書いて出します。

【この層の約束】
ここは**純粋**。ファイルもDBも触らず、渡された数を比べるだけ。
渡される合計は `float | None` で、`None` は**シートが持っていない**。
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
    #: シートが持っていないので比べられなかった項目。**×ではありません**
    skipped: list[str] = field(default_factory=list)
    #: シート自身の食い違い(合計式が古い、など)。**読み取りの×ではありません**
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """**比べた項目が全部合っていれば ○。**

        1つも比べられなかったとき(`no_totals`)は ○ にしません ──
        確かめていないので。
        """
        if self.no_totals or not self.lines:
            return False
        return all(line.ok for line in self.lines)

    @property
    def bad(self) -> list[Line]:
        return [line for line in self.lines if not line.ok]

    def _tail(self) -> str:
        """合計の後ろに付ける、言っておくべきこと。"""
        parts = list(self.notes)
        if self.skipped:
            parts.append("シートが持っていないので比べていない項目: "
                         + " / ".join(self.skipped))
        return ("。" + "。".join(parts)) if parts else ""

    def summary(self) -> str:
        if self.no_totals:
            return "シートに合計が入っていないので、突き合わせられません"
        if not self.lines:
            return ("比べられる合計がシートにありません"
                    + self._tail().lstrip("。"))
        if self.ok:
            return f"{len(self.lines)}項目すべて一致" + self._tail()
        return (f"{len(self.bad)}/{len(self.lines)}項目がちがいます ── "
                + " / ".join(line.text() for line in self.bad[:4])
                + self._tail())

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "no_totals": self.no_totals,
                "summary": self.summary(), "skipped": list(self.skipped),
                "notes": list(self.notes),
                "lines": [line.as_dict() for line in self.lines]}


def _add(report: Report, what: str, read_value: float,
         sheet_value: float | None, tolerance: float) -> None:
    """1項目ぶん。**シートが持っていなければ比べません。**"""
    if sheet_value is None:
        report.skipped.append(what)
        return
    report.lines.append(Line(what, read_value, sheet_value, tolerance))


def _sum_or_none(values) -> float | None:
    """**1つでも欠けていたら `None`。** 足せた分だけ比べると嘘になります。"""
    collected = list(values)
    if not collected or any(v is None for v in collected):
        return None
    return float(sum(collected))


def _day(report: Report, what: str, read_value: float,
         sheet_value: float | None, direct: float | None,
         tolerance: float) -> None:
    """日計。**シートが直合計と食い違うときは、直計の合計と比べます。**"""
    if (sheet_value is not None and direct is not None
            and abs(direct - sheet_value) > tolerance):
        report.notes.append(
            f"シートの{what}({sheet_value:g})が直合計の足し算({direct:g})と"
            "食い違っています(ブックの合計式が計算し直されていません)。"
            "直合計のほうと比べました")
        sheet_value = direct
    _add(report, what, read_value, sheet_value, tolerance)


def build(name: str, *, read_by_shift: dict, read_stops: float,
          sheet_by_shift: dict, sheet_day: tuple, sheet_stops: dict) -> Report:
    """突き合わせを組む。

    `read_by_shift`  … 直 → (枚数, 重量)。**明細を足したもの**
    `sheet_by_shift` … 直 → (枚数, 重量)。**シートの合計欄**(`None` は空欄)
    `sheet_day`      … (枚数, 重量)。その日の合計(`None` は空欄)
    `sheet_stops`    … 分類 → 分(`None` は空欄)。3つ足したものと比べる
    """
    report = Report(name=name)
    shift_counts = [c for c, _w in sheet_by_shift.values()]
    shift_weights = [w for _c, w in sheet_by_shift.values()]
    if not any(value is not None for value in
               shift_counts + shift_weights + list(sheet_day)
               + list(sheet_stops.values())):
        report.no_totals = True
        return report

    for shift in sorted(set(read_by_shift) | set(sheet_by_shift)):
        read_c, read_w = read_by_shift.get(shift, (0.0, 0.0))
        sheet_c, sheet_w = sheet_by_shift.get(shift, (None, None))
        _add(report, f"{shift} 枚数", read_c, sheet_c, COUNT_TOLERANCE)
        _add(report, f"{shift} 重量", read_w, sheet_w, WEIGHT_TOLERANCE)

    read_day_c = sum(c for c, _w in read_by_shift.values())
    read_day_w = sum(w for _c, w in read_by_shift.values())
    _day(report, "日計 枚数", read_day_c, sheet_day[0],
         _sum_or_none(shift_counts), COUNT_TOLERANCE)
    _day(report, "日計 重量", read_day_w, sheet_day[1],
         _sum_or_none(shift_weights), WEIGHT_TOLERANCE)
    _add(report, "停止 合計(分)", read_stops,
         _sum_or_none(sheet_stops.values()), MINUTE_TOLERANCE)
    return report
