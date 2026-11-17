r"""板集計のグラフ ── まとめた集計から**タイル**を組み立てる

日報管理ツール(all-tools)の `presenters/dashboard.py` からの移植です。
あちらは共有DBに残してある集計を読みますが、こちらは**まとめたCSVの行**を
読みます ── 出どころが同じ(集計)なので、同じ数・同じ絵になります。

【決めるのはここ(サーバ)です】
何を出すか・単位・丸め・並び・色の意味・データが無いときの文言まで、
判断はこの層が持ちます。画面(`app/static/js/chart.js`)は受け取った
「ラベルと値の並び」を座標へ写すだけです ── 絵の描き方が変わっても、
何を見せるかは動きません。

【色は「意味」で渡します】
`mark` / `tone` に渡すのは `qty`(枚数のなかま)・`dur`(時間のなかま)・
`stop`(停止)・`cat-1`〜(見分けるための色)といった**意味**で、色そのもの
ではありません。何色かは `tokens.css` が1か所で決めます。

【並べて見る】
VBA の `graphF` は1枚ずつ切り替える作りでしたが、それだと隣が見えません
──「重量は出ているのに稼働率が低い」は並べて初めて分かります。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import plate_source

#: タイルの種類(画面はこれを見て描き方を決める)
KIND_NUMBER = "number"
KIND_BAR = "bar"
KIND_LINE = "line"
KIND_DONUT = "donut"
KIND_TABLE = "table"
KIND_COMBO = "combo"

#: 量の種類(色の意味)。**色名ではありません**
TONE_QTY = "qty"
TONE_DUR = "dur"
TONE_STOP = "stop"
TONE_GOAL = "goal"
TONE_CUMULATIVE = "cumulative"

#: 並べて見分けるための色。輪は長い順に並ぶので、長いものが `cat-1`
CAT_TONES = tuple(f"cat-{i}" for i in range(1, 10))
CAT_REST = "cat-10"

#: 輪に出す項目の上限。**多すぎると読めない**ので、超えたぶんは「その他」へ
DONUT_LIMIT = 6

#: 直の色。**Excelのグラフと同じ 青・赤・緑・紫**(入れ替え禁止)
SHIFT_CLASSES = {
    "1直": "shift-1", "2直": "shift-2", "3直": "shift-3", "日勤": "shift-day",
    "4直": "cat-7", "5直": "cat-9",
}

#: 直の並び(紙に出る順)。ここに無い直は後ろへ五十音で
SHIFT_ORDER = ("1直", "2直", "3直", "日勤", "4直", "5直")

#: 1日ぶんの分数。稼働率を出し直すのに使います
MINUTES_PER_DAY = 24 * 60

#: 停止3分類+その他の列(`plate_source.AGGREGATE_COLUMNS` と同じ字)
STOP_KIND_COLUMNS = (
    ("管理ロス設備停止(分)", "管理ロス設備停止"),
    ("段取り・突発停止(分)", "段取り・突発停止"),
    ("ハンドリング停止(分)", "ハンドリング停止"),
    ("その他(分)", "その他"),
)

#: 停止の分類ごとの色。**分類は日によって入れ替わらない**ので固定します
STOP_KIND_TONES = {
    "管理ロス設備停止": "cat-1", "段取り・突発停止": "cat-2",
    "ハンドリング停止": "cat-3", "その他": CAT_REST,
}


def fixed(value: float, digits: int) -> str:
    """数を字にする。**整数は小数点を出しません。**"""
    if digits <= 0:
        return f"{round(value):,}"
    return f"{value:,.{digits}f}"


@dataclass
class Series:
    """複合グラフの1本。**単位が違うものを1枚に重ねるためのもの。**"""

    key: str
    title: str
    kind: str                            # bar / line
    unit: str = ""
    axis: str = "left"                   # left / right
    stack: str = ""
    values: list[float] = field(default_factory=list)
    mark: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "kind": self.kind,
                "unit": self.unit, "axis": self.axis, "values": self.values,
                "stack": self.stack, "mark": self.mark}


@dataclass
class Tile:
    """タイル1枚。**画面はこれを写すだけ。**"""

    key: str
    title: str
    kind: str
    note: str = ""
    unit: str = ""
    value: str = ""
    labels: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    slices: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    total: str = ""
    empty: str = "この期間のデータはありません"
    tone: str = ""
    point_classes: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        if self.kind == KIND_NUMBER:
            return self.value != ""
        if self.kind == KIND_DONUT:
            return bool(self.slices)
        if self.kind == KIND_TABLE:
            return bool(self.rows)
        if self.kind == KIND_COMBO:
            return any(any(v for v in s.values) for s in self.series)
        return any(v for v in self.values)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "kind": self.kind,
                "note": self.note, "unit": self.unit, "value": self.value,
                "labels": self.labels, "values": self.values,
                "series": [s.as_dict() for s in self.series],
                "slices": self.slices, "columns": self.columns,
                "rows": self.rows, "total": self.total, "empty": self.empty,
                "has_data": self.has_data, "tone": self.tone,
                "point_classes": self.point_classes}


# ----------------------------------------------------------------------
# 読み取り ── CSVの行を、数の取りやすい形へ
# ----------------------------------------------------------------------
@dataclass
class ShiftRow:
    """集計CSVの1行(1日・1ライン・1直)。**数にしたもの。**"""

    date: str
    line: str
    shift: str
    sheets: float = 0.0
    weight: float = 0.0
    work: float = 0.0
    operating: float = 0.0
    stop: float = 0.0
    kinds: dict[str, float] = field(default_factory=dict)
    rate: float = 0.0
    productivity: float = 0.0


def shift_rows(rows: list[dict[str, str]]) -> list[ShiftRow]:
    """まとめた集計の行を読む。**空欄は 0**(`plate_source.number`)。"""
    num = plate_source.number
    found: list[ShiftRow] = []
    for row in rows:
        one = ShiftRow(
            date=row.get("日付", ""), line=row.get("ライン", ""),
            shift=row.get("直", ""),
            sheets=num(row.get("合計枚数")), weight=num(row.get("合計重量(t)")),
            work=num(row.get("作業時間合計(分)")),
            operating=num(row.get("稼働時間合計(分)")),
            stop=num(row.get("停止合計(分)")),
            rate=num(row.get("稼働率(%)")),
            productivity=num(row.get("生産性(t/h)")),
        )
        one.kinds = {label: num(row.get(column))
                     for column, label in STOP_KIND_COLUMNS}
        found.append(one)
    return found


def shift_sort_key(name: str) -> tuple[int, str]:
    """直の並び。**紙に出る順**で、知らない直は後ろへ五十音で。"""
    try:
        return (SHIFT_ORDER.index(name), "")
    except ValueError:
        return (len(SHIFT_ORDER), name)


def _by_date(rows: list[ShiftRow]) -> dict[str, list[ShiftRow]]:
    found: dict[str, list[ShiftRow]] = {}
    for row in rows:
        found.setdefault(row.date, []).append(row)
    return found


def day_rate_pct(rows: list[ShiftRow]) -> float:
    """その日の稼働率(%)。**直ごとの平均ではなく、合計から出し直す。**

    直の平均にすると、1直だけ動いた日と3直とも動いた日が同じ数字に
    なります(移植元 `logic/aggregation.day_rate_pct` と同じ出し方)。
    """
    if not rows:
        return 0.0
    stopped = sum(r.stop for r in rows)
    whole = float(MINUTES_PER_DAY * len(rows))
    return max(0.0, (whole - stopped) / whole * 100)


# ----------------------------------------------------------------------
# タイル
# ----------------------------------------------------------------------
def _number(key: str, title: str, note: str, value: float, unit: str,
            tone: str, digits: int = 0) -> Tile:
    return Tile(key=key, title=title, kind=KIND_NUMBER, note=note, unit=unit,
                value=fixed(value, digits), tone=tone)


def total_tiles(rows: list[ShiftRow], note: str) -> list[Tile]:
    """期間の合計(大きな数字)。**最初に目に入るもの。**"""
    days = _by_date(rows)
    rate = (sum(day_rate_pct(one) for one in days.values()) / len(days)
            if days else 0.0)
    return [
        _number("weight", "合計重量", note, sum(r.weight for r in rows), "t",
                TONE_QTY, 2),
        _number("sheets", "合計枚数", note, sum(r.sheets for r in rows), "枚",
                TONE_QTY),
        _number("rate", "稼働率(日の平均)", note, rate, "%", TONE_DUR, 1),
        _number("stop", "停止合計", note, sum(r.stop for r in rows), "分",
                TONE_STOP),
    ]


def trend_tiles(rows: list[ShiftRow], lines: list[str]) -> list[Tile]:
    """日別の推移。**ラインが2つ以上なら、ライン別に重ねます。**

    1ラインのときに折れ線1本で描くのは、凡例が要らないぶん読みやすい
    からです。2つ以上のときは**比べるために**重ねます ── 別々のタイルに
    すると、目を往復させることになります。
    """
    days = _by_date(rows)
    labels = sorted(days)
    if not labels:
        return []

    if len(lines) <= 1:
        weights = [sum(r.weight for r in days[d]) for d in labels]
        rates = [round(day_rate_pct(days[d]), 1) for d in labels]
        sheets = [sum(r.sheets for r in days[d]) for d in labels]
        return [
            Tile(key="trend_weight", title="日別の重量推移", kind=KIND_LINE,
                 unit="t", labels=labels, values=weights, tone=TONE_QTY),
            Tile(key="trend_rate", title="日別の稼働率推移", kind=KIND_LINE,
                 unit="%", labels=labels, values=rates, tone=TONE_DUR),
            Tile(key="trend_sheets", title="日別の枚数推移", kind=KIND_LINE,
                 unit="枚", labels=labels, values=sheets, tone=TONE_QTY),
        ]

    def per_line(pick) -> list[Series]:
        found: list[Series] = []
        for index, line in enumerate(lines):
            values = []
            for day in labels:
                same = [r for r in days[day] if r.line == line]
                values.append(pick(same))
            found.append(Series(key=f"line-{line}", title=line, kind="line",
                                mark=CAT_TONES[index % len(CAT_TONES)],
                                values=values))
        return found

    return [
        Tile(key="trend_weight", title="日別の重量推移(ライン別)",
             kind=KIND_COMBO, unit="t", labels=labels,
             series=[Series(**{**s.as_dict(), "unit": "t"})
                     for s in per_line(lambda rs: round(sum(r.weight for r in rs), 2))]),
        Tile(key="trend_rate", title="日別の稼働率推移(ライン別)",
             kind=KIND_COMBO, unit="%", labels=labels,
             series=[Series(**{**s.as_dict(), "unit": "%"})
                     for s in per_line(lambda rs: round(day_rate_pct(rs), 1))]),
        Tile(key="trend_sheets", title="日別の枚数推移(ライン別)",
             kind=KIND_COMBO, unit="枚", labels=labels,
             series=[Series(**{**s.as_dict(), "unit": "枚"})
                     for s in per_line(lambda rs: sum(r.sheets for r in rs))]),
    ]


def cumulative_tile(rows: list[ShiftRow]) -> Tile:
    """その日の枚数(棒)と累積枚数(線)。**目盛りは左右で別。**

    その日の枚数は数百、累積は数万になるので、同じ目盛りに乗せると
    棒が床に貼り付いて高さの差が読めません。
    """
    days = _by_date(rows)
    labels = sorted(days)
    daily = [sum(r.sheets for r in days[d]) for d in labels]
    running: list[float] = []
    total = 0.0
    for value in daily:
        total += value
        running.append(total)
    return Tile(
        key="trend_cumulative", title="日別の枚数と累積枚数", kind=KIND_COMBO,
        unit="枚", labels=labels,
        series=[
            Series(key="count", title="その日の枚数", kind="bar", unit="枚",
                   axis="left", values=daily, mark=TONE_QTY),
            Series(key="cumulative", title="累積枚数", kind="line", unit="枚",
                   axis="right", values=running, mark=TONE_CUMULATIVE),
        ])


def shift_tiles(rows: list[ShiftRow]) -> list[Tile]:
    """直別の棒。**棒1本ずつを直の色で塗ります**(色は画面が持つ)。"""
    names = sorted({r.shift for r in rows if r.shift}, key=shift_sort_key)
    if not names:
        return []
    marks = [SHIFT_CLASSES.get(name, "") for name in names]
    weights = [round(sum(r.weight for r in rows if r.shift == name), 2)
               for name in names]
    stops = [sum(r.stop for r in rows if r.shift == name) for name in names]
    return [
        Tile(key="shift_weight", title="直別の重量", kind=KIND_BAR, unit="t",
             labels=names, values=weights, tone=TONE_QTY, point_classes=marks),
        Tile(key="shift_stop", title="直別の停止時間", kind=KIND_BAR, unit="分",
             labels=names, values=stops, tone=TONE_STOP, point_classes=marks),
    ]


def stop_kind_tile(rows: list[ShiftRow]) -> Tile:
    """停止の内訳(分類)の輪。**分類の色は固定**(日によって入れ替えません)。"""
    pairs = [(label, sum(r.kinds.get(label, 0.0) for r in rows))
             for _, label in STOP_KIND_COLUMNS]
    pairs = [(label, value) for label, value in pairs if value > 0]
    total = sum(value for _, value in pairs)
    slices = [{"label": label, "value": round(value),
               "pct": round(value / total * 100, 1) if total else 0,
               "mark": STOP_KIND_TONES.get(label, "")}
              for label, value in sorted(pairs, key=lambda p: -p[1])]
    return Tile(key="stop_kinds", title="停止の内訳(分類)", kind=KIND_DONUT,
                unit="分", slices=slices, total=fixed(total, 0),
                tone=TONE_STOP, empty="この期間に停止はありません")


def stop_item_tile(stops: list[dict[str, str]]) -> Tile:
    """停止の項目ごとの輪。**手が打てるのは項目まで降りたとき**だけです。

    材料は停止内訳CSVの「1件ずつ」ではなく**直の合計**の行です ──
    1件ずつも足すと二重に数えます。
    """
    totals: dict[str, float] = {}
    for row in stops:
        if row.get("区分") and row.get("区分") != "直の合計":
            continue
        name = (row.get("内訳") or row.get("記号") or "").strip()
        if not name:
            continue
        totals[name] = totals.get(name, 0.0) + plate_source.number(
            row.get("停止時間(分)"))
    ordered = sorted(((name, value) for name, value in totals.items()
                      if value > 0), key=lambda p: -p[1])
    rest = 0.0
    if len(ordered) > DONUT_LIMIT:
        rest = sum(value for _, value in ordered[DONUT_LIMIT:])
        ordered = ordered[:DONUT_LIMIT]
    total = sum(value for _, value in ordered) + rest
    slices = [{"label": name, "value": round(value),
               "pct": round(value / total * 100, 1) if total else 0,
               "mark": CAT_TONES[index % len(CAT_TONES)]}
              for index, (name, value) in enumerate(ordered)]
    if rest > 0:
        slices.append({"label": "その他(まとめ)", "value": round(rest),
                       "pct": round(rest / total * 100, 1) if total else 0,
                       "mark": CAT_REST})
    return Tile(key="stop_items", title="停止の項目ごと", kind=KIND_DONUT,
                unit="分", slices=slices, total=fixed(total, 0),
                tone=TONE_STOP,
                note=f"長い順に {DONUT_LIMIT} 項目まで(残りはまとめ)",
                empty="停止内訳CSVがありません")


def day_table_tile(rows: list[ShiftRow]) -> Tile:
    """日別の表。**グラフで見当を付けて、数はここで読みます。**"""
    days = _by_date(rows)
    table: list[list[str]] = []
    for day in sorted(days):
        same = days[day]
        table.append([
            day, "／".join(sorted({r.line for r in same})),
            str(len(same)),
            fixed(sum(r.sheets for r in same), 0),
            fixed(sum(r.weight for r in same), 2),
            fixed(sum(r.stop for r in same), 0),
            fixed(day_rate_pct(same), 1),
        ])
    return Tile(key="day_table", title="日別の集計(表)", kind=KIND_TABLE,
                columns=["日付", "ライン", "直の数", "枚数", "重量(t)",
                         "停止(分)", "稼働率(%)"],
                rows=table)


def stop_table_tile(stops: list[dict[str, str]]) -> Tile:
    """停止の項目(表)。輪に出なかった項目も**全部**出します。"""
    totals: dict[tuple[str, str], list[float]] = {}
    for row in stops:
        if row.get("区分") and row.get("区分") != "直の合計":
            continue
        name = (row.get("内訳") or row.get("記号") or "").strip()
        if not name:
            continue
        key = (row.get("分類", ""), name)
        found = totals.setdefault(key, [0.0, 0.0])
        found[0] += plate_source.number(row.get("停止時間(分)"))
        found[1] += plate_source.number(row.get("回数")) or 1
    table = [[kind, name, fixed(value[0], 0), fixed(value[1], 0)]
             for (kind, name), value in sorted(totals.items(),
                                               key=lambda p: -p[1][0])]
    return Tile(key="stop_table", title="停止の項目(表)", kind=KIND_TABLE,
                columns=["分類", "内訳", "停止時間(分)", "回数"], rows=table)


def build(collected: plate_source.Collected, *, note: str = "") -> dict:
    """まとめたものから、画面に並べるタイルを全部作る。

    並びがそのまま画面の並びです ── **大きな数字 → 推移 → 内訳 → 表**。
    上から「どうだったか」「どう動いたか」「何に時間を取られたか」
    「で、数は」の順に読めるようにしてあります。
    """
    rows = shift_rows(collected.rows)
    tiles: list[Tile] = []
    tiles.extend(total_tiles(rows, note))
    tiles.extend(trend_tiles(rows, collected.lines))
    tiles.append(cumulative_tile(rows))
    tiles.extend(shift_tiles(rows))
    tiles.append(stop_kind_tile(rows))
    tiles.append(stop_item_tile(collected.stops))
    tiles.append(day_table_tile(rows))
    tiles.append(stop_table_tile(collected.stops))
    return {"note": note, "tiles": [t.as_dict() for t in tiles]}
