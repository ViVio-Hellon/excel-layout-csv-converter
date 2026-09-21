"""機側データ集計 ── VBA「機側データ集計」と同じ読み取りと転記

【何をするものか】
日報ブックを何冊も読み、明細行を**1枚の表に積み上げて**CSVに書きます。
VBA(`UF.CommandButton1_Click` → `機側データ集計`)が Excel のシートへ
やっていたことを、そのままCSVへ出すだけです。

    ブック何冊 → 対象シートを選ぶ → 8行目から拾う → 1枚の表へ積む
               → 空欄を2通りのやり方で埋める → CSV

**グラフは作りません**(VBA側のおまけ機能)。読み取りと転記だけです。

【停止3枠(Stop3)のみ】
VBA はチェックボックスで停止2枠/3枠を選べますが、実物のブック(2026年の
機側)は3枠で、`docs/座標表.md` の座標と**完全に一致**していました。
2枠は列が2つずれた別テンプレートなので、ここでは扱いません ── 読めない
ものを黙って読んだことにしないため、**見出しの位置が違えば断ります**。

【順番が結果を変えます】
空欄の埋め方は2通りあり、**やる順番で結果が変わります**。ロット№を先に
上詰めで埋め、そのロットを鍵にして他の列を埋めるからです。VBA と同じ順で
行います(`_fill_all` の並びがその順序です)。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .books import NotXlsx, read_sheets

#: 拾い始める行。上は見出し(1〜7行目)
FIRST_ROW = 8

#: 最終行を決める列。**VBAと同じ**(終了時刻の「時」)
LAST_ROW_COLUMN = "L"

#: 対象シートの条件。**VBAと同じ**
MARK_CELL = "A1"
MARK_VALUE = "集計"
NAME_MARK = "_機側"

#: 見出しと、その値を取る列(停止3枠)。**並び順がそのままCSVの列順**です
COLUMNS: tuple[tuple[str, str], ...] = (
    ("ロット№", "D"),
    ("材・調質", "E"),
    ("サイズ", "F"),
    ("検入", "I"),
    ("用途コード", "AE"),
    ("用途名", "AF"),
    ("納入先", "AG"),
    ("梱包数", "P"),
    ("包数", "Q"),
    ("作業時間（分）", "AC"),
    ("作業停止内訳-1", "S"),
    ("時間（分）", "T"),
    ("作業停止内訳-2", "U"),
    ("時間（分）", "V"),
    ("作業停止内訳-3", "W"),
    ("時間（分）", "X"),
    ("etc", "AI"),
    ("包装仕様NO", "AH"),
    ("作業人数", "N"),
    ("縦割", "AQ"),
    ("横割", "AR"),
    ("係数処理ﾛｯﾄ数", "B"),
)

#: CSVの見出し。1列目は日付(シート名から採る)。
#: **「時間（分）」が3回出ます** ── VBAの見出しをそのまま使っているためで、
#: 直すと「VBAの集計シートと同じもの」ではなくなります
HEADERS: tuple[str, ...] = ("日付",) + tuple(name for name, _col in COLUMNS)

# 表の中の位置(0始まり)。VBA の列番号との対応は docs/機側データ集計.md
LOT = 1                      # ロット№(ロット単位の補完はこれを鍵にする)
_FILL_DOWN = (1, 2, 3, 4, 5, 6)          # ロット№〜用途名(VBA: 2〜7列)
_FILL_DOWN_WORKERS = 19                  # 作業人数(VBA: 20列)
_FILL_BY_LOT_TO = 7                      # 納入先(VBA: 8列)
_FILL_BY_LOT = (17, 18, 20, 21, 22)      # etc・包装仕様NO・縦割・横割・係数処理ﾛｯﾄ数

#: 見出しの確かめ。**ここがずれていたら別テンプレート**なので読みません
CHECK_CELLS: tuple[tuple[str, str], ...] = (
    ("S7", "内容"), ("T7", "時間"), ("U7", "内容"), ("V7", "時間"),
    ("W7", "内容"), ("X7", "時間"), ("AC7", "作業時間"),
    ("AE7", "用途コード"), ("AF7", "用途名"), ("AG7", "納入先"),
    ("AH7", "包装仕様NO"),
)


@dataclass
class SheetResult:
    """シート1枚ぶんの結果。"""

    file: str = ""
    sheet: str = ""
    date: str = ""
    rows: int = 0
    last_row: int = 0
    skipped: str = ""          # 拾わなかった理由(空なら拾った)

    @property
    def taken(self) -> bool:
        return not self.skipped


@dataclass
class Tally:
    """積み上げた表ぜんぶ。"""

    rows: list[list[str]] = field(default_factory=list)
    sheets: list[SheetResult] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)   # (ファイル名, 理由)

    @property
    def taken(self) -> list[SheetResult]:
        return [s for s in self.sheets if s.taken]

    @property
    def dates(self) -> list[str]:
        """拾った日付を、出てきた順に(重複なし)。"""
        found: list[str] = []
        for sheet in self.taken:
            if sheet.date and sheet.date not in found:
                found.append(sheet.date)
        return found

    def summary(self) -> str:
        if not self.rows:
            return "拾える行がありませんでした"
        days = sorted(self.dates, key=_sort_key)
        span = f"{days[0]} 〜 {days[-1]}" if len(days) > 1 else (days[0] if days else "")
        return (f"{len(self.taken)} シートから {len(self.rows)} 行"
                + (f"({span})" if span else ""))


_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def _sort_key(text: str) -> tuple:
    """日付の並べ替え用。読めない書き方は後ろへ回します。"""
    found = _DATE.match(text)
    if not found:
        return (1, text)
    return (0, f"{found.group(1)}.{int(found.group(2)):02d}.{int(found.group(3)):02d}")


# ------------------------------------------------------------------
# 拾う
# ------------------------------------------------------------------
def wanted(row: dict) -> bool:
    """この行を拾うか。**VBAの条件そのまま。**

    作業の行(終了時刻が入っている)に加えて、**停止の対だけが書かれた行**も
    拾います ── 「内径カット 30分」のように、作業時間の欄が空で停止だけが
    書かれる行が実際にあります(VBAが後から直された理由がこれです)。
    """
    return bool(row.get(LAST_ROW_COLUMN)
                or (row.get("S") and row.get("T"))
                or (row.get("U") and row.get("V"))
                or (row.get("W") and row.get("X")))


def last_row_of(rows: dict) -> int:
    """最終行。**L列に何か入っている一番下の行**(VBAの `End(xlUp)`)。

    L列が1つも無ければ 1 を返します ── VBA もそうなり、`8 To 1` の繰り返しは
    1度も回りません。停止だけの行があっても拾われません。**同じにします。**
    """
    found = [number for number, values in rows.items()
             if values.get(LAST_ROW_COLUMN)]
    return max(found) if found else 1


def sheet_date(sheet_name: str) -> str:
    """日付。**シート名の「_」より前**(VBA: `Split(ws.Name, "_")(0)`)。"""
    return sheet_name.split("_")[0]


def layout_problem(sheet) -> str:
    """見出しが停止3枠の並びか。違えば理由を返します(空なら合っている)。"""
    for ref, expected in CHECK_CELLS:
        text = sheet.cell(ref).replace("\n", "")
        if expected not in text:
            return (f"見出しが違います({ref} は「{expected}」のはずが "
                    f"「{text or '空'}」)。停止3枠のブックだけ読めます")
    return ""


def take_sheet(sheet, *, file_name: str = "") -> tuple[list[list[str]], SheetResult]:
    """シート1枚から行を拾う。**VBAの転記そのまま。**"""
    result = SheetResult(file=file_name, sheet=sheet.name,
                         date=sheet_date(sheet.name))
    if sheet.cell(MARK_CELL) != MARK_VALUE or NAME_MARK not in sheet.name:
        result.skipped = f"対象外({MARK_CELL}が「{MARK_VALUE}」で名前に{NAME_MARK}を含むシートだけ)"
        return [], result

    problem = layout_problem(sheet)
    if problem:
        result.skipped = problem
        return [], result

    rows = sheet.rows
    result.last_row = last_row_of(rows)
    taken: list[list[str]] = []
    previous_lot = ""                     # **シートごとに持ち直します**
    for number in range(FIRST_ROW, result.last_row + 1):
        values = rows.get(number, {})
        if not wanted(values):
            continue
        line = [result.date] + [values.get(col, "") for _name, col in COLUMNS]
        # A列のロット。実物では空ですが、VBA が見ているので同じに見ます
        current = values.get("A", "")
        if current:
            previous_lot = current
        if not line[LOT] and previous_lot:
            line[LOT] = previous_lot
        taken.append(line)
    result.rows = len(taken)
    return taken, result


# ------------------------------------------------------------------
# 空欄を埋める ── **順番が結果を変えます**
# ------------------------------------------------------------------
def fill_down(rows: list[list[str]], index: int) -> None:
    """上の行の値で埋める(VBA: `空白を埋める汎用版`)。

    **先頭が空のときは、最初に見つかった値**で埋めます ── VBA が
    「上の値」を最初の非空セルで初期化しているのと同じです。
    """
    seed = ""
    for row in rows:
        if row[index]:
            seed = row[index]
            break
    value = seed
    for row in rows:
        if row[index]:
            value = row[index]
        else:
            row[index] = value


def fill_by_lot(rows: list[list[str]], index: int) -> None:
    """同じロットで**最初に現れた値**で埋める(VBA: `etc埋め`)。

    上の行は見ません ── 納入先のように、ロットが変われば別の値になるもの
    を上から引きずらないためです。
    """
    first: dict[str, str] = {}
    for row in rows:
        lot = row[LOT]
        if row[index] and lot and lot not in first:
            first[lot] = row[index]
    for row in rows:
        if not row[index] and row[LOT] in first:
            row[index] = first[row[LOT]]


def _fill_all(rows: list[list[str]]) -> None:
    """VBA と同じ順番で埋める。**並べ替えないでください。**"""
    for index in _FILL_DOWN:              # ①ロット№〜用途名を上詰め
        fill_down(rows, index)
    fill_by_lot(rows, _FILL_BY_LOT_TO)    # ②納入先はロット単位
    fill_down(rows, _FILL_DOWN_WORKERS)   # ③作業人数は上詰め(前のLotを見る)
    for index in _FILL_BY_LOT:            # ④etc・包装仕様NO・縦割・横割・係数
        fill_by_lot(rows, index)


# ------------------------------------------------------------------
# まとめる
# ------------------------------------------------------------------
def build(paths, *, on_file=None) -> Tally:
    """ブックを順に読み、1枚の表にする。

    `paths` の順がそのまま**積む順**です(上詰めの補完は並び順で結果が
    変わるので、呼ぶ側が名前順に並べてから渡してください)。
    `on_file` は1冊終わるたびに呼ばれます(進み具合を出すため)。
    """
    tally = Tally()
    paths = [Path(p) for p in paths]
    for index, path in enumerate(paths, start=1):
        try:
            sheets = read_sheets(path)
        except (NotXlsx, OSError) as exc:
            tally.errors.append((path.name, str(exc)))
            if on_file:
                on_file(path, index, len(paths), None)
            continue
        last: SheetResult | None = None
        for sheet in sheets:
            rows, result = take_sheet(sheet, file_name=path.name)
            tally.rows.extend(rows)
            tally.sheets.append(result)
            last = result
        if on_file:
            on_file(path, index, len(paths), last)
    _fill_all(tally.rows)
    return tally


# ------------------------------------------------------------------
# 書き出す
# ------------------------------------------------------------------
def file_stem(tally: Tally) -> str:
    """出すCSVの名前。**日付の幅が名前で分かる**ようにします。"""
    days = [_DATE.match(d) for d in tally.dates]
    stamps = [f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}"
              for m in days if m]
    if not stamps:
        return "機側データ集計"
    if len(stamps) == 1:
        return f"機側データ集計_{stamps[0]}"
    return f"機側データ集計_{min(stamps)}-{max(stamps)}"


def write_csv(tally: Tally, out_dir) -> Path:
    """CSVを1本書いて、その道を返す。

    BOM付き UTF-8 ── Windows の Excel でそのまま開けます(既存のCSVと同じ)。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{file_stem(tally)}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(tally.rows)
    return path
