"""機側データ集計 ── VBA「機側データ集計」と同じ読み取りと転記

【何をするものか】
日報ブックを何冊も読み、明細行を**1枚の表に積み上げて**CSVに書きます。
VBA(`UF.CommandButton1_Click` → `機側データ集計`)が Excel のシートへ
やっていたことを、そのままCSVへ出すだけです。

    ブック何冊 → 対象シートを選ぶ → 8行目から拾う → 1枚の表へ積む
               → 空欄を2通りのやり方で埋める → CSV

**グラフは作りません**(VBA側のおまけ機能)。読み取りと転記だけです。

【CSVも受けます】
ブック(.xlsx / .xlsm)のほかに、CSVも読めます。中身を見て2通りに分けます。

    1行目がこの見出しと同じ   … **前に出した集計**。そのまま積み増します
    A1 が「集計」            … シートをCSVで保存したもの。ブックと同じに読みます

どちらでもなければ**断ります** ── 中身の分からないCSVを黙って混ぜると、
できあがった表が何なのか誰にも言えなくなります。

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
import os
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
    """入力1つぶん(シート1枚 / CSV1本)の結果。"""

    file: str = ""
    sheet: str = ""
    date: str = ""
    #: どのラインのものか(シート名の「_」より後、など)。**分けて出すため**
    line: str = ""
    rows: int = 0
    last_row: int = 0
    skipped: str = ""          # 拾わなかった理由(空なら拾った)
    #: 拾ったけれど言っておくこと(内訳名が引けなかった記号など)
    note: str = ""
    kind: str = "シート"        # シート / CSV(シート) / CSV(集計) / CSV(集計明細)

    @property
    def taken(self) -> bool:
        return not self.skipped


@dataclass
class Tally:
    """積み上げた表ぜんぶ。"""

    rows: list[list[str]] = field(default_factory=list)
    #: `rows` と同じ長さの、行ごとのライン。**CSVには出しません**
    #: (VBAの23列のままにするため)。分けて出すのと、空欄の埋め方に使います
    lines: list[str] = field(default_factory=list)
    sheets: list[SheetResult] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)   # (ファイル名, 理由)
    #: シート名の `_機側` を問わずに読んだか。**VBAと違う読み方をしたしるし**
    any_sheet: bool = False

    def groups(self) -> dict:
        """ライン → その行だけ(出てきた順)。**行そのものを渡します**
        (list は参照なので、ここに入れたまま埋めれば元にも効きます)。
        """
        found: dict[str, list[list[str]]] = {}
        for index, row in enumerate(self.rows):
            line = self.lines[index] if index < len(self.lines) else ""
            found.setdefault(line, []).append(row)
        return found

    def add(self, rows: list, line: str) -> None:
        """拾った行を、ライン付きで積む。"""
        self.rows.extend(rows)
        self.lines.extend([line] * len(rows))

    @property
    def taken(self) -> list[SheetResult]:
        return [s for s in self.sheets if s.taken]

    @property
    def dates(self) -> list[str]:
        """拾った日付を、出てきた順に(重複なし)。

        **行から採ります** ── 前に出した集計CSVを積み増したときは、
        1本の中に何日ぶんも入っているためです。
        """
        found: list[str] = []
        for row in self.rows:
            if row and row[0] and row[0] not in found:
                found.append(row[0])
        return found

    @property
    def notes(self) -> list[str]:
        """拾ったけれど言っておくこと。**黙って飲み込みません。**"""
        found: list[str] = []
        for one in self.sheets:
            if one.note and one.note not in found:
                found.append(one.note)
        return found

    def summary(self) -> str:
        if not self.rows:
            return "拾える行がありませんでした" + self._tail()
        days = sorted(self.dates, key=_sort_key)
        span = f"{days[0]} 〜 {days[-1]}" if len(days) > 1 else (days[0] if days else "")
        return (f"{len(self.taken)} シートから {len(self.rows)} 行"
                + (f"({span})" if span else "") + self._tail())

    def _tail(self) -> str:
        notes = list(self.notes)
        if self.any_sheet:
            # **黙って広げません。** VBAは機側のシートだけを見ます
            notes.insert(0, "機側以外のシートも読みました"
                            "(VBAは名前に「_機側」を含むシートだけ見ます)")
        if not self.rows and self.name_skipped:
            # **1行も拾えなかったときだけ**言います ── 拾えているのに
            # 「機側以外ばかりでした」と出ると、何が起きたのか分かりません
            notes.append("機側以外のシートばかりでした ──「機側以外のシートも"
                         "読む」を入れると、同じ様式なら読めます")
        return ("。" + "。".join(notes)) if notes else ""

    @property
    def name_skipped(self) -> bool:
        """シート名の条件だけで外れたものがあるか(言い添えるため)。"""
        return any(NAME_MARK in one.skipped for one in self.sheets)


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


def sheet_line(sheet_name: str) -> str:
    """ラインの呼び名。**シート名の「_」より後**(`2026年8月23日_機側` → `機側`)。

    VBAは日付しか使いません(機側しか見ないので要らなかった)。ほかのラインも
    読むときは、**どのラインの行かが分からないと混ざります** ── 空欄の
    埋め方は上の行を見るので、板の行が機側の値を引き継いでしまいます。
    """
    _date, _, rest = sheet_name.partition("_")
    return rest.strip()


def line_name(text: str) -> str:
    """現場の呼び名 → ツールのライン名(`機側` → `LS`)。**当たらなければそのまま。**

    日報管理ツールの当て方をそのまま借ります ── 同じラインの行が、
    ブックからは「機側」、変換したCSVからは「LS」として別々に分かれて
    しまわないように。
    """
    folded = (text or "").strip()
    if not folded:
        return ""
    try:
        from nippou.logic import nippou_sheet
        return nippou_sheet.guess_line(folded) or folded
    except Exception:                             # noqa: BLE001 - 名前はそのまま
        return folded


def layout_problem(sheet) -> str:
    """見出しが停止3枠の並びか。違えば理由を返します(空なら合っている)。"""
    for ref, expected in CHECK_CELLS:
        text = sheet.cell(ref).replace("\n", "")
        if expected not in text:
            return (f"見出しが違います({ref} は「{expected}」のはずが "
                    f"「{text or '空'}」)。停止3枠のブックだけ読めます")
    return ""


#: 停止2枠のブックの目印。停止の枠が2つ(S・T / U・V)しか無いぶん、
#: **その右の列が3枠より2つ左へずれます**(作業時間 AC→AA、用途コード AE→AC)
TWO_SLOT_CELLS: tuple[tuple[str, str], ...] = (
    ("S7", "内容"), ("T7", "時間"), ("U7", "内容"), ("V7", "時間"),
    ("AA7", "作業時間"), ("AC7", "用途コード"),
)

FRAME_THREE = "3枠"
FRAME_TWO = "2枠"


def stop_frame(sheet) -> str:
    """停止の枠がいくつのテンプレートか。`3枠` / `2枠` / `""`(分からない)。

    **読めるのは3枠だけ**ですが、読めなかったときに「2枠だから」と言えると
    探す手間が違います ── 「3枠なのに読めない」ものを見つけたいので。
    """
    if not layout_problem(sheet):
        return FRAME_THREE
    if all(expected in sheet.cell(ref).replace("\n", "")
           for ref, expected in TWO_SLOT_CELLS):
        return FRAME_TWO
    return ""


#: 拾わなかった訳の種類。**数えるときに、名前の条件と様式の違いを混ぜない**
WHY_NAME = "name"      # シート名に `_機側` が無い(ほかのライン。VBAと同じ)
WHY_MARK = "mark"      # A1 が「集計」でない
WHY_FRAME = "frame"    # 停止2枠のテンプレート
WHY_LAYOUT = "layout"  # 見出しがどこか違う


def why_kind(sheet, *, any_sheet: bool = False) -> str:
    """`why_skipped` の理由を、**種類**で返す(空なら拾えます)。"""
    if not any_sheet and NAME_MARK not in sheet.name:
        return WHY_NAME
    if sheet.cell(MARK_CELL) != MARK_VALUE:
        return WHY_MARK
    if stop_frame(sheet) == FRAME_TWO:
        return WHY_FRAME
    return WHY_LAYOUT if layout_problem(sheet) else ""


def why_skipped(sheet, *, any_sheet: bool = False) -> str:
    """このシートを拾わない理由。**空なら拾えます。**

    条件を1つずつ見て、**どれで外れたのかと、実際の値**を言います ──
    「対象外」とだけ出ていると、直しようがありません。

    `any_sheet` は**シート名の縛りだけ**を外します(頼まれたときだけ)。
    VBAは `InStr(ws.Name, "_機側") > 0` で機側のシートしか見ません ──
    マクロが機側ライン用に作られたものだからです。紙の様式は同じなので、
    座標はほかのラインでもそのまま合いますが、**外すとVBAと同じ結果では
    なくなります**。だから既定は切ってあり、使ったことは結果に書きます。
    """
    if not any_sheet and NAME_MARK not in sheet.name:
        return (f"シート名に「{NAME_MARK}」がありません(名前: 「{sheet.name}」)。"
                "VBAも同じ条件でシートを選びます"
                "(ほかのラインも読むなら「機側以外のシートも読む」)")
    mark = sheet.cell(MARK_CELL)
    if mark != MARK_VALUE:
        return (f"{MARK_CELL}が「{MARK_VALUE}」ではありません"
                f"(いまは「{mark or '空'}」)。VBAも同じ条件でシートを選びます")
    if stop_frame(sheet) == FRAME_TWO:
        return ("停止2枠のブックです(作業時間が AA 列 ── 3枠より2つ左に"
                "ずれています)。読めるのは停止3枠だけです")
    return layout_problem(sheet)


#: 読めるのに1行も拾えないときの言い分。**0行も「読めない」のうち**です
EMPTY_NOTE = ("拾える行がありません(終了時刻のL列が空なので、VBAも"
              "1行も拾いません)")


def count_rows(rows: dict) -> int:
    """このシートから拾える行数(VBAと同じ条件・同じ範囲)。"""
    return sum(1 for number in range(FIRST_ROW, last_row_of(rows) + 1)
               if wanted(rows.get(number, {})))


def check_sheet(sheet, *, file_name: str = "", any_sheet: bool = False) -> dict:
    """1枚ぶんの**点検結果**。読む相手を探すときに使います(拾いません)。"""
    why = why_skipped(sheet, any_sheet=any_sheet)
    rows = sheet.rows
    found = count_rows(rows)
    return {"file": file_name, "sheet": sheet.name,
            "mark": sheet.cell(MARK_CELL), "frame": stop_frame(sheet),
            "ok": not why, "why": why or (EMPTY_NOTE if not found else ""),
            "kind": why_kind(sheet, any_sheet=any_sheet),
            "line": line_name(sheet_line(sheet.name)),
            "empty": not why and not found,
            "last_row": last_row_of(rows), "rows": found}


def check_book(path, *, any_sheet: bool = False) -> list[dict]:
    """ブック1冊の全シートを点検する。読めなければ理由を1件返します。"""
    try:
        sheets = read_sheets(Path(path))
    except (NotXlsx, OSError) as exc:
        return [{"file": Path(path).name, "sheet": "", "mark": "", "frame": "",
                 "ok": False, "why": str(exc), "kind": "broken", "line": "",
                 "last_row": 0, "rows": 0}]
    return [check_sheet(sheet, file_name=Path(path).name, any_sheet=any_sheet)
            for sheet in sheets]


def take_sheet(sheet, *, file_name: str = "",
               any_sheet: bool = False) -> tuple[list[list[str]], SheetResult]:
    """シート1枚から行を拾う。**VBAの転記そのまま。**"""
    result = SheetResult(file=file_name, sheet=sheet.name,
                         date=sheet_date(sheet.name),
                         line=line_name(sheet_line(sheet.name)))
    why = why_skipped(sheet, any_sheet=any_sheet)
    if why:
        result.skipped = why
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
    if not taken:
        # **0行も言っておきます。** ○ と出ているのに何も増えないのは、
        # 「読めた」と「拾えた」が別だと分かっていないと気づけません
        result.note = EMPTY_NOTE
    return taken, result


# ------------------------------------------------------------------
# CSV を読む
# ------------------------------------------------------------------
#: CSVの文字コード。**この順に試します** ── Excelの「CSV(コンマ区切り)」は
#: 日本語Windowsでは CP932 で保存されます
ENCODINGS = ("utf-8-sig", "cp932", "utf-8")


def _column_name(index: int) -> str:
    """0 → A、25 → Z、26 → AA。"""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def read_grid(path) -> list[list[str]]:
    """CSVを表として読む。**読めた文字コードで通します。**"""
    last: Exception | None = None
    for encoding in ENCODINGS:
        try:
            text = Path(path).read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            last = exc
            continue
        return [[cell.strip() for cell in row]
                for row in csv.reader(text.splitlines())]
    raise ValueError(f"文字コードが読めません({last})")


def grid_rows(grid: list[list[str]]) -> dict:
    """表を `{行番号: {"D": "値"}}` にする(シートと同じ形)。"""
    rows: dict[int, dict[str, str]] = {}
    for number, line in enumerate(grid, start=1):
        values = {_column_name(i): cell for i, cell in enumerate(line) if cell}
        if values:
            rows[number] = values
    return rows


class _Grid:
    """CSVを、シートと同じ顔つきで扱えるようにする小さな入れ物。"""

    def __init__(self, name: str, rows: dict) -> None:
        self.name = name
        self._rows = rows

    @property
    def rows(self) -> dict:
        return self._rows

    def cell(self, ref: str) -> str:
        """`"E1"` の形で1つ取る(シートの `cell()` と同じ呼び方)。"""
        column = "".join(char for char in ref if char.isalpha())
        number = "".join(char for char in ref if char.isdigit())
        if not number:
            return ""
        return self._rows.get(int(number), {}).get(column, "")


def take_csv(path, *, grid=None, stops: "StopNames | None" = None
             ) -> tuple[list[list[str]], SheetResult]:
    """CSV1本から行を拾う。**中身(1行目)を見て種類を決めます。**

    `stops` は停止内訳CSVから作った記号→内訳名の対応です(あれば使います)。
    """
    path = Path(path)
    result = SheetResult(file=path.name, sheet=path.name, kind="CSV")
    if grid is None:
        try:
            grid = read_grid(path)
        except (ValueError, OSError) as exc:
            result.skipped = str(exc)
            return [], result
    if not grid:
        result.skipped = "空のCSVです"
        return [], result

    head = [cell for cell in grid[0]]
    if head[:len(HEADERS)] == list(HEADERS):
        return _take_tallied(grid, result)

    if (grid[0][0] if grid[0] else "") == MARK_VALUE:
        return _take_grid_sheet(path, grid, result)

    kind = csv_kind(head)
    if kind == DETAIL_CSV:
        return _take_detail_csv(grid, result, stops)
    if kind == STOPS_CSV:
        result.kind = "CSV(停止内訳)"
        result.note = ("停止の記号→内訳名として使いました"
                       "(集計の行は集計明細CSVかブックから拾います)")
        return [], result
    if kind:
        result.skipped = (
            f"{kind}のCSVです。機側データ集計の材料になるのは"
            "**集計明細**のCSVかブックです(同じ日のフォルダに入っています)")
        return [], result

    result.skipped = ("中身が分かりません。入れられるのは ①日報のブック "
                      f"②変換が出した集計明細のCSV ③シートをCSVで保存したもの"
                      f"(A1が「{MARK_VALUE}」) ④前に出した機側データ集計のCSV "
                      "です")
    return [], result


# ------------------------------------------------------------------
# 変換が出したCSVを、そのまま集計の材料にする
#
# 変換すると1日につき4本出ます(`集計` `集計明細` `停止内訳` `計算内容`)。
# 現場は**日付フォルダごとドラッグ**するので、4本とも入ってきます。
#
#   集計明細 … **行の出どころ。** 1行=1行で、シートの8行目以降と同じ中身
#   停止内訳 … 記号(リ・E…)の**内訳名**。集計明細には記号しか入っていません
#   集計・計算内容 … 日ごとのまとめ。行の材料にはならないので、理由を言って飛ばす
#
# 【見出しの名前をここに書き写しません】
# 列の名前(「梱包数量 個装単位 枚数」など)は移行元の定義から組み立てます
# (`nippou.layout` と `nippou.logic.nippou_sheet`)。書き写すと、向こうで
# 名前を直したときに**こちらだけ古くなり**、黙って拾えなくなります。
# ------------------------------------------------------------------
#: CSVの種類の呼び名。**画面にそのまま出ます**
DETAIL_CSV = "集計明細"
STOPS_CSV = "停止内訳"
DAY_CSV = "日ごとの集計"
FORMULA_CSV = "計算内容"

#: 停止3枠。シートは「内容(S/U/W)+時間(T/V/X)」ですが、集計明細CSVには
#: **記号(AJ/AL/AN)+時間(AK/AM/AO)しかありません**。同じ枠として扱います
STOP_SLOTS: tuple[tuple[str, str, str, str], ...] = (
    ("S", "T", "AJ", "AK"),
    ("U", "V", "AL", "AM"),
    ("W", "X", "AN", "AO"),
)

#: 終了時刻の「時」。**拾う行かどうかはここで決めます**(シートのL列と同じ)
END_HOUR_COLUMN = "L"

StopNames = dict


def _vendor() -> tuple:
    """移行元の定義(見出しと列の対応)を、まとめて借りる。"""
    from nippou import layout
    from nippou.logic.nippou_sheet import COLUMNS as FIELD_OF
    from nippou.logic import formula
    from nippou.reporting import csv_export
    return layout, FIELD_OF, formula, csv_export


def detail_labels() -> dict[str, str]:
    """シートの列 → 集計明細CSVの見出し。**移行元の定義から組みます。**"""
    layout, field_of, _formula, _csv_export = _vendor()
    label_of = {column.family.lower(): layout.csv_header(column)
                for column in (layout.COLUMNS + layout.EXTRA_COLUMNS)}
    found: dict[str, str] = {}
    for column, field in field_of.items():
        label = label_of.get(field)
        if label:
            found[column] = label
    return found


def csv_kind(head: list[str]) -> str:
    """1行目から、変換が出したどのCSVかを決める。**分からなければ空。**"""
    _layout, _field_of, formula, csv_export = _vendor()
    known = ((DETAIL_CSV, list(csv_export.DETAIL_FIELDNAMES)),
             (STOPS_CSV, list(csv_export.STOP_FIELDNAMES)),
             (DAY_CSV, list(csv_export.FIELDNAMES)),
             (FORMULA_CSV, list(formula.CSV_FIELDNAMES)))
    for name, fields in known:
        if head[:len(fields)] == fields:
            return name
    # 列が増えた・減らされた版でも見分けられるように、**要る列がそろって
    # いるか**で見ます(名前を書き写さず、上の定義から取った名前で見ます)
    inside = set(head)
    for name, fields in known:
        needed = set(fields[:5] + fields[-1:])
        if needed and needed <= inside:
            return name
    return ""


def stop_names(grid: list[list[str]]) -> StopNames:
    """停止内訳CSVから、記号→内訳名の対応を作る。

    **行ごとの対応(日付・直・ページ・行・停止の位置)を先に見ます** ──
    同じ記号でも、内訳名を付け替えた日があり得るので。見つからなければ
    記号だけで引きます。内訳名が空の行は入れません(変換したときに
    参照用マスタが読めていないとき)。
    """
    if not grid:
        return {}
    head = grid[0]
    at = {name: index for index, name in enumerate(head)}
    needed = ("日付", "直", "ページ", "行", "停止の位置", "記号", "内訳")
    if any(name not in at for name in needed):
        return {}
    found: StopNames = {}
    for line in grid[1:]:
        if len(line) < len(head):
            line = list(line) + [""] * (len(head) - len(line))
        label = line[at["内訳"]].strip()
        code = line[at["記号"]].strip()
        if not label or not code:
            continue
        found.setdefault(("記号", code), label)
        where = line[at["停止の位置"]].strip()
        if where:
            found[(line[at["日付"]].strip(), line[at["直"]].strip(),
                   line[at["ページ"]].strip(), line[at["行"]].strip(),
                   where)] = label
    return found


def _slot_label(index: int) -> str:
    """停止の位置の呼び名(`作業停止①`)。**移行元の定義から。**"""
    _layout, _field_of, _formula, csv_export = _vendor()
    return csv_export.STOP_SLOT_LABELS.get(index, "")


def _take_detail_csv(grid: list[list[str]], result: SheetResult,
                     stops: StopNames | None):
    """**変換が出した集計明細CSV**を、シートと同じに読む。

    拾う行の条件は VBA と同じです ── 終了時刻が入っている行、または
    停止(記号と時間)の対が書かれている行(`wanted`)。

    停止の枠には、シートなら**内訳名**(S/U/W)が入っています。集計明細CSVに
    あるのは**記号**だけなので、同じ日の停止内訳CSVがあればそこから内訳名を
    引きます。引けない記号は**記号のまま入れて、そう言います**(黙って
    空にしません)。
    """
    result.kind = "CSV(集計明細)"
    stops = stops or {}
    head = grid[0]
    if "ライン" in head:
        # 1本は1日・1ラインぶんなので、**先頭の行のライン**で足ります
        where = head.index("ライン")
        for line in grid[1:]:
            if len(line) > where and line[where].strip():
                result.line = line_name(line[where].strip())
                break
    at = {name: index for index, name in enumerate(head)}
    labels = detail_labels()

    def source(column: str, name: str) -> int:
        """シートの列に当たるCSVの位置。**無ければ -1。**"""
        label = labels.get(column, "")
        if label in at:
            return at[label]
        return at.get(name, -1)          # 「係数処理ﾛｯﾄ数」のように名前が同じもの

    places = {column: source(column, name) for name, column in COLUMNS}
    end_hour = source(END_HOUR_COLUMN, "")
    taken: list[list[str]] = []
    unknown: list[str] = []

    for line in grid[1:]:
        if not any(cell.strip() for cell in line):
            continue
        if len(line) < len(head):
            line = list(line) + [""] * (len(head) - len(line))

        def value(column: str) -> str:
            place = places.get(column, -1)
            return line[place].strip() if 0 <= place < len(line) else ""

        values = {column: value(column) for _name, column in COLUMNS}
        # 停止は「記号+時間」から。内訳名が引ければ名前に替えます
        for index, (content, minutes, code_col, time_col) in enumerate(
                STOP_SLOTS, start=1):
            code_at, time_at = source(code_col, ""), source(time_col, "")
            code = line[code_at].strip() if 0 <= code_at < len(line) else ""
            span = line[time_at].strip() if 0 <= time_at < len(line) else ""
            name = ""
            if code:
                key = (line[at["日付"]].strip() if "日付" in at else "",
                       line[at["直"]].strip() if "直" in at else "",
                       line[at["ページ"]].strip() if "ページ" in at else "",
                       line[at["行"]].strip() if "行" in at else "",
                       _slot_label(index))
                name = stops.get(key) or stops.get(("記号", code)) or ""
                if not name:
                    name = code
                    if code not in unknown:
                        unknown.append(code)
            values[content] = name
            values[minutes] = span

        if not ((0 <= end_hour < len(line) and line[end_hour].strip())
                or any(values.get(c) and values.get(m)
                       for c, m, _s, _t in STOP_SLOTS)):
            continue
        date = line[at["日付"]].strip() if "日付" in at else ""
        taken.append([date] + [values.get(column, "")
                               for _name, column in COLUMNS])

    result.rows = len(taken)
    result.date = taken[0][0] if taken else ""
    if unknown:
        result.note = ("停止の内訳名が分からない記号は、記号のまま入れました: "
                       + " ".join(unknown[:8])
                       + "(同じ日の停止内訳CSVを一緒に入れるか、設定部の"
                         "「停止内訳CSVの置き場所」を指してください)")
    return taken, result


#: 出すCSVの名前の頭。`機側データ集計_<ライン>_<日付>.csv`
FILE_HEAD = "機側データ集計"


def line_of_name(stem: str) -> str:
    """`機側データ集計_LS_2026.08.23` → `LS`。読めなければ空。

    前に出した集計CSVには**ラインの列がありません**(VBAの23列のまま)。
    名前に入れてあるので、積み増すときはそこから拾います。
    """
    parts = stem.split("_")
    if len(parts) >= 3 and parts[0] == FILE_HEAD:
        return parts[1]
    return ""


def _take_tallied(grid: list[list[str]], result: SheetResult):
    """**前に出した集計**。そのまま積み増します(VBAの追記と同じ)。"""
    result.kind = "CSV(集計)"
    result.line = line_name(line_of_name(Path(result.file).stem))
    width = len(HEADERS)
    rows: list[list[str]] = []
    for line in grid[1:]:
        if not any(line):
            continue
        if len(line) > width:
            result.skipped = (f"列が多すぎます({len(line)} 列。"
                              f"集計は {width} 列です)")
            return [], result
        rows.append(list(line) + [""] * (width - len(line)))
    result.rows = len(rows)
    result.date = rows[0][0] if rows else ""
    return rows, result


def _take_grid_sheet(path: Path, grid: list[list[str]], result: SheetResult):
    """**シートをCSVで保存したもの**。ブックと同じに読みます。

    日付はシート名から採れないので、**作業日の欄(E1)**を使います ──
    実物のブックでは、シート名の日付と同じ文字が入っています。
    無ければファイル名で代えます。
    """
    result.kind = "CSV(シート)"
    rows = grid_rows(grid)
    sheet = _Grid(path.name, rows)
    # ラインは**梱包ラインの欄(E2)**から。シート名が無いので
    result.line = line_name(sheet.cell("E2"))
    problem = layout_problem(sheet)
    if problem:
        result.skipped = problem
        return [], result

    result.date = sheet.cell("E1") or path.stem
    result.last_row = last_row_of(rows)
    taken: list[list[str]] = []
    previous_lot = ""
    for number in range(FIRST_ROW, result.last_row + 1):
        values = rows.get(number, {})
        if not wanted(values):
            continue
        line = [result.date] + [values.get(col, "") for _name, col in COLUMNS]
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
def _scan_csv(paths: list[Path]) -> tuple[dict, StopNames]:
    """CSVを先に1度読み、表と**停止の内訳名**を集める。

    読めなかったものは**ここでは何も言いません** ── そのまま
    `take_csv` が読み直して、理由を1か所で言います(二重に言わない)。
    """
    grids: dict[Path, list[list[str]]] = {}
    stops: StopNames = {}
    for path in paths:
        if path.suffix.lower() != ".csv":
            continue
        try:
            grid = read_grid(path)
        except (ValueError, OSError):
            continue
        grids[path] = grid
        if grid and csv_kind(grid[0]) == STOPS_CSV:
            for key, label in stop_names(grid).items():
                stops.setdefault(key, label)
    # **受け皿は設定部の停止内訳CSV(無ければ参照用マスタ)。**
    # 一緒に入れた停止内訳CSVのほうが先 ── その日に使われていた名前なので
    for code, label in master_names().items():
        stops.setdefault(("記号", code), label)
    return grids, stops


def master_names() -> dict:
    """設定部の停止内訳CSV / 参照用マスタからの 記号 → 内訳名。

    読めなければ空を返します ── 名前が引けないことを理由に集計を
    止めません(記号のまま入れて、そう言います)。
    """
    try:
        from . import stop_reasons
        return stop_reasons.codes()
    except Exception:                             # noqa: BLE001 - 無しで続ける
        return {}


def build(paths, *, on_file=None, any_sheet: bool = False) -> Tally:
    """ブックを順に読み、1枚の表にする。

    `paths` の順がそのまま**積む順**です(上詰めの補完は並び順で結果が
    変わるので、呼ぶ側が名前順に並べてから渡してください)。
    `on_file` は1冊終わるたびに呼ばれます(進み具合を出すため)。渡すのは
    **その1冊から出た結果の全部**です ── 1冊に何枚も入っていることがあり、
    最後の1枚だけ渡すと「拾われなかった残りの枚数」が画面から消えます。

    **CSVは先に1度のぞきます**(`_scan_csv`) ── 停止内訳CSVの内訳名を、
    集計明細CSVを読むときに使うためです。入れた順(ドラッグの順)に左右
    されないように、先に全部集めます。

    `any_sheet` を立てると、**シート名の `_機側` を問いません**(A1が「集計」で
    停止3枠なら読みます)。VBAは機側のシートだけを見るので、**立てた時点で
    VBAと同じ結果ではなくなります** ── 使ったことは結果に書きます。
    """
    tally = Tally(any_sheet=any_sheet)
    paths = [Path(p) for p in paths]
    grids, stops = _scan_csv(paths)
    for index, path in enumerate(paths, start=1):
        if path.suffix.lower() == ".csv":
            rows, result = take_csv(path, grid=grids.get(path), stops=stops)
            tally.add(rows, result.line)
            tally.sheets.append(result)
            if on_file:
                on_file(path, index, len(paths), [result])
            continue
        try:
            sheets = read_sheets(path)
        except (NotXlsx, OSError) as exc:
            tally.errors.append((path.name, str(exc)))
            if on_file:
                on_file(path, index, len(paths), [])
            continue
        made: list[SheetResult] = []
        for sheet in sheets:
            rows, result = take_sheet(sheet, file_name=path.name,
                                      any_sheet=any_sheet)
            tally.add(rows, result.line)
            tally.sheets.append(result)
            made.append(result)
        if on_file:
            on_file(path, index, len(paths), made)
    # **ラインごとに埋めます。** 上詰めもロット単位も「上の行」を見るので、
    # 混ぜたまま埋めると板の行に機側の値が入ります
    for rows in tally.groups().values():
        _fill_all(rows)
    return tally


# ------------------------------------------------------------------
# 書き出す
# ------------------------------------------------------------------
def file_stem(tally: Tally, line: str = "", rows: list | None = None) -> str:
    """出すCSVの名前。**ラインと日付の幅が名前で分かる**ようにします。

        機側データ集計_LS_2026.08.23.csv
        機側データ集計_HVC_2026.08.23-2026.08.27.csv
        機側データ集計_2026.08.23.csv          ← ラインが分からないとき

    ラインを名前に入れるのは、**ライン別に分けて出す**からです(機側には
    板に無い欄があるので、1本にまとめると空の列が混ざります)。
    """
    found = tally.dates if rows is None else _dates_of(rows)
    days = [_DATE.match(d) for d in found]
    stamps = [f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}"
              for m in days if m]
    parts = [FILE_HEAD]
    if line:
        parts.append(line)
    if len(stamps) == 1:
        parts.append(stamps[0])
    elif stamps:
        parts.append(f"{min(stamps)}-{max(stamps)}")
    return "_".join(parts)


def _dates_of(rows: list) -> list[str]:
    """その行たちに出てくる日付を、出てきた順に(重複なし)。"""
    found: list[str] = []
    for row in rows:
        if row and row[0] and row[0] not in found:
            found.append(row[0])
    return found


def write_csv(tally: Tally, out_dir) -> Path:
    """**1本目**のCSVを書いて、その道を返す(ラインが1つのときの近道)。

    中身は `write_csvs` と同じです ── 2ライン以上あるときは**全部書いて**
    1本目を返すので、呼ぶ側が気づかずに取りこぼすことはありません。

    BOM付き UTF-8 ── Windows の Excel でそのまま開けます(既存のCSVと同じ)。

    【いきなり本番のファイルへ書きません】
    `open(..., "w")` は**開いた瞬間に中身を消します。** 書いている途中で
    失敗すると(共有が切れた・Excelで開かれていた・容量が尽きた)、前に
    出したCSVが**途中まで**の姿で残ります。出来上がったものを置き換える
    形にすれば、失敗しても前のCSVはそのままです。

    同じフォルダに仮の名前で書いてから置き換えます ── 別のフォルダに
    書くと、置き換えが1回の操作で済まず(別ボリュームだと写しになる)、
    途中の姿が見える隙ができます。

    【Excelで開いたままだと書けません】
    Windowsでは、開かれているファイルは置き換えられません。**前のCSVは
    無事のまま**なので、閉じてからやり直せます。そのことが分かる言葉で
    断ります。
    """
    return write_csvs(tally, out_dir)[0]


def write_csvs(tally: Tally, out_dir) -> list[Path]:
    """**ラインごとに1本**書いて、その道を順に返す。

    機側には板に無い欄(コイル縦割・横割・係数処理ﾛｯﾄ数)があり、板には
    機側に無い欄(合紙・ＶＣ種別)があります。1本にまとめると、どちらの行にも
    相手の空の列が並びます ── **ラインで分けて出します**。

    列の並びは**どのラインでも同じ23列**(VBAの集計シートのまま)です。
    使わない欄が空になるだけで、様式は変えません。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = tally.groups()
    return [_write_one(rows, out_dir / f"{file_stem(tally, line, rows)}.csv")
            for line, rows in groups.items()]


def _write_one(rows: list, path: Path) -> Path:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            writer.writerows(rows)
        os.replace(tmp, path)
    except PermissionError as exc:
        raise PermissionError(
            f"{path.name} を書けませんでした。Excelなどで開いたままに"
            f"なっていませんか(前に出したCSVはそのまま残っています): {exc}"
        ) from exc
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return path
