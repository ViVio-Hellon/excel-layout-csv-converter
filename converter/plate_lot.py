r"""ロットを探す ── 期間とロット№で、集計明細CSVから当たった行を出す

日報管理ツール(all-tools)の `presenters/lot_search.py` からの移植です。
向こうは保存した日報(DB)を読みますが、**こちらは板データ参照パスの
`集計明細_*.csv`** を読みます ── 置いてある材料が違うだけで、やることと
言葉は同じにしてあります。

【なぜ要るか】(向こうの言葉のまま)

    期間を絞ってロットNo検索したい場合に何を検索したらいいですか?
    「ロットを探す」を作ってください。CSV に出す機能も付けます

専用の探し方が無いと、年月のフォルダを開いて → 日のフォルダを開いて →
CSVを開いて Ctrl+F、を日数ぶん繰り返すことになります。ここは
**期間とロット№(頭の数桁だけでも)を入れて1回**で出します。

【なぜ集計明細か】
1行=1ロットで、紙の欄がそのまま入っているからです(`ロット№`・材・寸法・
検入枚数・開始/終了・実績・作業者・用途名・納入先)。集計CSVは直ごとの
合計なので、ロットの行がありません。

【一致のしかた】
**前方一致**(頭の数桁)。打った字も、CSVの字も同じ直し方をしてから
比べます(全角→半角・小文字→大文字・英数字以外を落とす・7桁まで ──
向こうの `input_rules.normalize("LOT", …)` と同じ)。`ｎ７１` でも
` n71 ` でも `N7131T0` に当たります。

**空欄は断ります。** 期間の全部を出すと、上限に当たって「多すぎる」と
言うだけになります。

【上限と並び】
並びは **作業日 → ライン → 直(1直・2直・3直・日勤の順)→ ページ → 行**。
紙をめくる順です。上限は `LIMIT` 行で、超えたら**先の `LIMIT` 行だけ**
出して「桁を増やすか期間を絞ってください」と言います。**CSVも画面と
同じ行**です(向こうと同じ ── 画面と違うものが出ると、どちらが本当か
分からなくなります)。
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import plate_graph, plate_paper, plate_source, report_db
from .logging_utils import get_logger

log = get_logger("plate.lot")

#: 集計明細CSVの頭(`集計明細_2026-11-17_L-1.csv`)
PREFIX_DETAIL = "集計明細_"

#: 画面に出す行の上限。**1画面で目で追える数**です
LIMIT = 200

#: ロット№の桁(日報入力の欄と同じ)。これより長い字は切って比べます
LOT_DIGITS = 7

#: ラインの欄の「全ライン」
ALL_LINES = "*"
ALL_LABEL = "全ライン"

#: 表の列。`(CSVの見出し, 画面の見出し, 数字の列か)`。
#: 画面の見出しは日報管理ツールの「ロットを探す」と同じ言葉にしてあります
COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("日付", "作業日", False),
    ("ライン", "ライン", False),
    ("直", "直", False),
    ("ページ", "ページ", True),
    ("行", "行", True),
    ("ロット№", "ロット№", False),
    ("材・調質", "材・調質", False),
    ("製品寸法 厚×幅×丈", "厚×幅×丈", False),
    ("検入枚数", "検入枚数", True),
    ("", "開始", False),                      # 時と分を1つにします(下の `_clock`)
    ("", "終了", False),
    ("実績合計 枚数", "実績枚数", True),
    ("実績合計 重量", "実績重量", True),
    ("作業時間(分)", "作業時間(分)", True),
    ("作業者", "作業者", False),
    ("用途名", "用途名", False),
    ("納入先", "納入先", False),
    # **どこから読んだ行か。** 集計に入っていないページ(集計から除く
    # 作業者)は紙からしか拾えないので、見ただけで分かるようにします
    ("", "どこから", False),
)

#: 「どこから」の字。**共有の日報データがいちばん確か**です
#: (全ライン・全端末の行が集まっていて、集計から除いたページも入っています)
FROM_SHARED = "共有の日報"
FROM_DETAIL = "集計明細"
FROM_PAPER = "印刷(集計に無い)"
FROM_PAPER_EXCLUDED = "印刷(集計から除く)"

#: 開始・終了は「時」と「分」が別の欄なので、`08:15` に組み直します
_CLOCK = {"開始": ("梱包作業時間 開始 時", "梱包作業時間 開始 分"),
          "終了": ("梱包作業時間 終了 時", "梱包作業時間 終了 分")}


def tidy_lot(text: str) -> str:
    """ロット№を比べる形へ(全角→半角・大文字・英数字だけ・7桁まで)。

    **捨てずに直します** ── IME が全角のままだった、貼り付けた元が全角
    だった、はどちらも現場で起きます。弾くと「打てているのに当たらない」
    になります。
    """
    out = unicodedata.normalize("NFKC", text or "").upper()
    out = "".join(ch for ch in out if ch.isascii() and ch.isalnum())
    return out[:LOT_DIGITS]


class BadRequest(ValueError):
    """画面に返す断り。`field` は直してほしい欄。"""

    def __init__(self, message: str, field_name: str = "") -> None:
        super().__init__(message)
        self.field_name = field_name


@dataclass
class Found:
    """当たった1行。"""

    date: str
    line: str
    shift: str
    row: dict[str, str]
    source: str = ""

    @property
    def lot(self) -> str:
        return self.row.get("ロット№", "")


@dataclass
class Result:
    """探した結果。**画面はこれを写すだけ**です。"""

    lot: str = ""
    columns: list[str] = field(default_factory=list)
    numeric: list[bool] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    #: 行ごとの鍵(`日付` `ライン` `直` `ページ`)。**画面の「日報を見る」が
    #: 使います** ── その行のあるページの紙(印刷用HTML)を開くため。
    #: 表の列には出しません(CSVにも出しません)
    keys: list[dict[str, str]] = field(default_factory=list)
    total: int = 0
    shown: int = 0
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    note: str = ""
    #: どこを探したか(画面に出します)。共有の日報データを読めたかどうかで変わります
    scope: str = ""
    skipped: list[dict[str, str]] = field(default_factory=list)
    files: int = 0

    def as_dict(self) -> dict:
        return {"lot": self.lot, "columns": self.columns, "keys": self.keys,
                "numeric": self.numeric, "rows": self.rows,
                "total": self.total, "shown": self.shown,
                "summary": self.summary, "warnings": self.warnings,
                "note": self.note, "scope": self.scope, "skipped": self.skipped,
                "files": self.files, "limit": LIMIT}


def _clock(row: dict[str, str], which: str) -> str:
    """`08:15`。片方しか無ければそのまま、どちらも無ければ空。"""
    hour, minute = (row.get(name, "").strip() for name in _CLOCK[which])
    if not hour and not minute:
        return ""
    return f"{hour or '--'}:{minute or '--'}"


def _cell(found: Found, key: str, label: str) -> str:
    if label == "どこから":
        return found.source
    if not key:
        return _clock(found.row, label)
    return (found.row.get(key) or "").strip()


def _sort_key(found: Found) -> tuple:
    """作業日 → ライン → 直 → ページ → 行(紙をめくる順)。"""
    def number(name: str) -> float:
        return plate_source.number(found.row.get(name))
    return (plate_source.sort_date(found.date), found.line,
            plate_graph.shift_sort_key(found.shift),
            number("ページ"), number("行"))


def search(base: Path, lines: list[str], start: date, end: date,
           lot: str, *, shared: Path | None = None) -> Result:
    r"""期間とロット№で探す。**読むだけ**で、何も書きません。

    `lines` が空なら、参照パス直下で集計を持っているライン全部です
    (画面の「全ライン」)。

    `shared` に共有の日報データ(`日報データ.sqlite3`)を渡すと、**そちらを
    先に読みます** ── 全ライン・全端末の行が集まっていて、日報管理ツールの
    「集計から除く」で外したページも入っているからです。読めなければ理由を
    出して、集計明細CSVと紙で続けます。
    """
    wanted = tidy_lot(lot)
    if not wanted:
        raise BadRequest("ロット№を入れてください"
                         "(頭の数桁だけでも探せます)", "lot")
    if start > end:
        start, end = end, start
    if not lines:
        lines = [one.name for one in plate_source.lines(base)
                 if one.has_aggregate]
    if not lines:
        raise BadRequest("参照パスの下にラインのフォルダがありません", "line")

    result = Result(lot=wanted)
    found: list[Found] = []
    #: すでに出どころのあるページ(日付, ライン, 直, ページ)。
    #: **上から順に強い**(共有 → 集計明細 → 紙)ので、下の出どころは
    #: ここに無いぶんだけ読みます
    covered: set[tuple[str, str, str, str]] = set()
    #: そのうち共有が持っていたページ(集計明細を飛ばすため)
    from_shared: set[tuple[str, str, str, str]] = set()
    found.extend(_from_shared(shared, lines, start, end, wanted, covered,
                              result))
    from_shared.update(covered)

    for line in lines:
        picked = [(day, folder) for day, folder in plate_source.days(base, line)
                  if start <= day <= end]
        if len(picked) > plate_source.MAX_DAYS:
            raise BadRequest(f"{line}: 期間が広すぎます"
                             f"({len(picked)} 日。一度に"
                             f" {plate_source.MAX_DAYS} 日までです)", "start")
        for day, folder in picked:
            stamp = plate_source.date_text(day)
            path = plate_source.find_csv(folder, PREFIX_DETAIL)
            if path is None:
                continue              # その日は明細が無い(集計だけの日もあります)
            rows, header, problem = plate_source.read_csv(path)
            result.files += 1
            if problem:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": f"集計明細CSVが{problem}"})
                continue
            if "ロット№" not in header:
                result.skipped.append({
                    "line": line, "date": stamp,
                    "reason": "集計明細CSVに「ロット№」の列がありません"})
                continue
            for row in rows:
                key = (stamp, line, row.get("直", ""),
                       str(row.get("ページ", "")).strip())
                if key in from_shared:
                    continue              # 共有から読んだページ(二重に出さない)
                # **当たらない行も、ページだけは覚えます** ── 紙を
                # 読むかどうかの判断に使います(下の `_from_paper`)
                covered.add(key)
                if not tidy_lot(row.get("ロット№", "")).startswith(wanted):
                    continue
                found.append(Found(date=row.get("日付") or stamp,
                                   line=row.get("ライン") or line,
                                   shift=row.get("直", ""), row=row,
                                   source=FROM_DETAIL))

    found.extend(_from_paper(base, lines, start, end, wanted, covered, result))
    found.sort(key=_sort_key)
    shown = found[:LIMIT]
    result.columns = [label for _, label, _ in COLUMNS]
    result.numeric = [numeric for _, _, numeric in COLUMNS]
    result.rows = [[_cell(one, key, label) for key, label, _ in COLUMNS]
                   for one in shown]
    result.keys = [{"date": one.date, "line": one.line, "shift": one.shift,
                    "page": (one.row.get("ページ") or "").strip()}
                   for one in shown]
    result.total = len(found)
    result.shown = len(shown)
    result.note = note_of(lines, start, end)
    if not result.scope:
        result.scope = ("集計明細CSVと紙(印刷用HTML)"
                        if shared is None else
                        "集計明細CSVと紙(印刷用HTML) ── 共有の日報データは"
                        "読めませんでした")
    else:
        result.scope = ("共有の日報データ(全ライン・全端末)"
                        " + 集計明細CSV + 紙(印刷用HTML)")

    if result.total:
        lots = {tidy_lot(one.lot) for one in found}
        shifts = {(one.date, one.line, one.shift) for one in found}
        spread = f"{len(lots)}ロット・{len(shifts)}直"
        if len({one.line for one in found}) > 1:
            spread += f"・{len({one.line for one in found})}ライン"
        result.summary = f"{wanted} で始まるロット: {result.total}行({spread})"
    if result.total > LIMIT:
        result.warnings.append(
            f"当たった行が {result.total}行 あり、多すぎるので先の {LIMIT}行"
            "だけ出しています。ロット№の桁を増やすか、期間を絞ってください")
    elif not result.total:
        result.warnings.append(
            f"{wanted} で始まるロットの行は、{result.note} にはありません"
            "(期間を広げるか、ラインを「全ライン」に、"
            "またはロット№の桁を減らしてください)")
    if result.files == 0:
        result.warnings.append(
            "集計明細CSVが1本もありませんでした ── 変換タブで出したぶんが"
            "参照パスに入っているか確かめてください"
            "(ロットの行は集計明細CSVにしかありません)")
    log.info("ロットを探す: %s 期間=%s〜%s ライン=%s → %d行(読んだCSV %d本)",
             wanted, plate_source.date_text(start), plate_source.date_text(end),
             "／".join(lines), result.total, result.files)
    return result


def _from_shared(shared: Path | None, lines: list[str], start: date, end: date,
                 wanted: str, covered: set, result: "Result") -> list[Found]:
    r"""**共有の日報データ**(`日報データ.sqlite3`)から拾う。

    ここが読めれば、日報管理ツールの「ロットを探す」と**同じ範囲**です
    ── 集計から除いたページも、紙を出していないページも入っています。
    読めなければ理由を画面に出して、集計明細CSVと紙で続けます
    (向こうも読めないときは手元で続けます)。
    """
    if shared is None:
        return []
    read = report_db.lot_rows(shared, lines, start, end,
                              lambda text: tidy_lot(text).startswith(wanted))
    result.files += 1
    if not read.ok:
        result.warnings.append(
            read.reason() + "、集計明細CSVと紙で探しました"
            "(集計から除いた作業者のページは、紙のあるぶんだけ出ます)")
        return []
    result.scope = FROM_SHARED
    if read.truncated:
        result.warnings.append(
            f"共有の日報データで当たった行が {report_db.CAP}行 を超えたので、"
            "そこで読むのをやめました。ロット№の桁を増やすか、期間を"
            "絞ってください")
    out: list[Found] = []
    for row in read.rows:
        covered.add((row["日付"], row["ライン"], row["直"], row["ページ"]))
        out.append(Found(date=row["日付"], line=row["ライン"],
                         shift=row["直"], row=row, source=FROM_SHARED))
    # **当たらなかったページも覆いたい**のですが、共有は当たった行しか
    # 持ってきません(そのほうが速い)。覆うのは当たった行のページだけで、
    # 同じページの同じ行が集計明細からも出ないようにします
    return out


def _from_paper(base: Path, lines: list[str], start: date, end: date,
                wanted: str, covered: set, result: "Result") -> list[Found]:
    r"""**集計に入っていないページ**を、紙(印刷用HTML)から拾う。

    日報管理ツールの「集計から除く」(除く作業者のいるページ)は、集計に
    入らないので集計明細CSVにも出ません。ページそのものは消えず、紙は
    残っているので、**集計明細に無かったページの紙だけ**を開きます ──
    ふだん(除外が無い日)は1枚も開きません。

    紙には用途名・納入先・作業時間の欄がありません(紙の印刷範囲の外)。
    空欄のまま出します ── 無い欄を埋めるより、**無いと分かる**ほうが
    親切です。
    """
    out: list[Found] = []
    opened = 0
    for line in lines:
        for stamp, name, shift, page, path in plate_paper.pages(
                base, line, start, end):
            if (stamp, line, shift, page) in covered:
                continue                  # 集計明細にあるページ(開きません)
            if opened >= plate_paper.MAX_FILES:
                result.warnings.append(
                    f"集計に無いページの紙が {plate_paper.MAX_FILES} 枚を"
                    "超えたので、そこで読むのをやめました(期間を絞ってください)")
                return out
            sheet = plate_paper.read(path)
            opened += 1
            result.files += 1
            if sheet is None:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": f"紙を読めません({path.name})"})
                continue
            source = (FROM_PAPER_EXCLUDED if sheet.excluded else FROM_PAPER)
            for row in sheet.rows:
                if not tidy_lot(row.get("ロット№", "")).startswith(wanted):
                    continue
                row = dict(row)
                row.setdefault("作業者", sheet.worker)
                # 紙の行には日付・ライン・直が入っていません(紙の頭に
                # 1つだけ書いてあります)。**行ごとに入れ直します** ──
                # 表に並べたときに空欄だと、どの日のものか分かりません
                row["日付"] = stamp
                row["ライン"] = name or line
                row["直"] = shift
                row["ページ"] = page
                out.append(Found(date=stamp, line=name or line, shift=shift,
                                 row=row, source=source))
    if opened:
        log.info("集計に無いページの紙を %d 枚読みました(当たり %d 行)",
                 opened, len(out))
    return out


def note_of(lines: list[str], start: date, end: date) -> str:
    """「2026-11-01 〜 2026-11-30 ／ L-1」。画面にも CSV にも同じ字を出します。"""
    span = plate_source.date_text(start)
    if start != end:
        span += f" 〜 {plate_source.date_text(end)}"
    return f"{span} ／ {'・'.join(lines) if lines else ALL_LABEL}"


def file_stem(lot: str, start: date, end: date) -> str:
    """`ロット検索_N71_2026-11-01-2026-11-30`。"""
    span = plate_source.date_text(start)
    if start != end:
        span += f"-{plate_source.date_text(end)}"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", f"ロット検索_{lot}_{span}").strip()
    return stem[:120] or "ロット検索"


def write_csv(result: Result, out_dir: Path, *, start: date, end: date) -> dict:
    r"""当たった行を CSV にする。**画面と同じ行**です。

    1行目に探した条件を書きます ── メールに添えた瞬間にフォルダから
    離れるので、何を探したぶんなのか分からないと使えません。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{file_stem(result.lot, start, end)}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([f"ロットを探す  {result.lot} で始まるロット",
                         result.note,
                         f"{result.total}行" if result.total <= LIMIT
                         else f"{result.total}行のうち先の {LIMIT}行"])
        writer.writerow([])
        writer.writerow(result.columns)
        writer.writerows(result.rows)
    log.info("ロット検索のCSVを書きました: %s (%d 行)", path, len(result.rows))
    return {"path": str(path), "name": path.name, "rows": len(result.rows),
            "total": result.total}
