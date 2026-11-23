r"""印刷用HTML(紙)を読む ── **集計に入っていないページも探せるように**

【なぜ要るか】
日報管理ツール(all-tools)には「**集計から除く**」があります。除く作業者の
いるページは**集計に入りません**(`services/agg_exclude`。除くのは
`summary.refresh_shift` ── 集計を作るところ)。集計CSV・集計明細CSV・
停止内訳CSVはどれもその集計から書かれるので、**除かれたページは3本とも
から外れます**。

    ページそのものは消しません・共有へもそのまま保存します
    (向こうの「ロットを探す」は共有の日報を読むので、除く人のページも出ます)

こちらの「ロットを探す」は集計明細CSVを読むので、**そのままでは除かれた
ロットが出ません**。けれど同じ参照パスに**紙(印刷用HTML)が残っています**
── 紙は打ったほうから作られ、共有へ保存したときと印刷したときに書かれます
(`services/paper_files`)。除いたページの紙には「**自動**」の印が付きます
(時間を自動で割り振っているため)。

    <参照パス>\L-1\印刷\2026.11\17\印刷_2026年11月17日_L-1_1直_1.html

そこで、**集計明細に無いページの紙だけ**を読んで補います。ファイル名に
日付・ライン・直・ページが入っているので、**開く前に**「集計明細にある
ページか」を見分けられます ── ふだんは1枚も開きません。

【読み方】
標準ライブラリの `html.parser` だけで読みます(追加ライブラリは入れません)。
欄の名前は**紙の見出しから組み立てます** ── 上段(まとめ)と下段(内訳)を
つないで `梱包作業時間 開始 時` のようにすると、**集計明細CSVの見出しと
同じ字**になります。列が増えても位置ではなく名前で拾えます。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from . import plate_source
from .logging_utils import get_logger

log = get_logger("plate.paper")

#: ラインフォルダの下の種類(`csv_export.KIND_PRINT` と同じ字)
KIND_PRINT = "印刷"

#: 紙のファイル名。`印刷_2026年11月17日_L-1_1直_1.html`
#: (ラインに `_` が入ることがあるので、**真ん中は欲張って**取ります)
FILE_NAME = re.compile(r"^印刷_(?P<date>[^_]+)_(?P<line>.+)_(?P<shift>[^_]+)_"
                       r"(?P<page>\d+)\.html$")

#: 紙の頭の「作業者名」。集計明細CSVの「作業者」に当たります
HEAD_WORKER = "作業者名"

#: 集計から除くページに付く印(`services/paper_files.AUTO_STAMP`)
AUTO_STAMP = "自動"

#: 1回に開く紙の上限。**開く前に絞っている**ので、ふつうはここまで行きません
MAX_FILES = 2000


@dataclass
class Sheet:
    """紙1枚ぶん(1ページ)。"""

    date: str = ""
    line: str = ""
    shift: str = ""
    page: str = ""
    worker: str = ""
    stamp: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def excluded(self) -> bool:
        """集計から除かれたページか(紙の「自動」の印で見ます)。"""
        return AUTO_STAMP in self.stamp


class _Reader(HTMLParser):
    r"""紙を読む。**見出し・頭の値・打ってある行**だけを拾います。

    `html.parser` は閉じ忘れにも強く、知らない字が混ざっていても
    止まりません ── 紙は人が開くものなので、荒れていても読めるだけ
    読みます。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sheets: list[Sheet] = []
        self._sheet: Sheet | None = None
        self._where = ""                  # head / thead / tbody / stamp
        self._text: list[str] = []
        self._head_line = 0               # 見出しの何段目か(0=上段・1=下段)
        self._span = (1, 1)
        self._top: list[tuple[str, int, int]] = []
        self._bottom: list[str] = []
        self._head_rows: list[list[str]] = []
        self._row: list[str] = []
        #: 明細の表を読み終えたか(そのあとの合計の表は読みません)
        self._table_done = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        names = (values.get("class") or "").split()
        if tag == "section" and "sheet" in names:
            self._sheet = Sheet()
            self._where = ""
            self._table_done = False
            self._top, self._bottom, self._head_rows = [], [], []
        elif tag == "table" and "head__keys" in names:
            self._where = "head"
        elif tag == "div" and "backfill-stamp" in names:
            self._where = "stamp"
            self._text = []
        elif tag == "thead" and not self._table_done:
            self._where = "thead"
            self._head_line = 0
        elif tag == "tbody" and not self._table_done:
            self._where = "tbody"
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._text = []
            self._span = (int(values.get("colspan") or 1),
                          int(values.get("rowspan") or 1))

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        text = "".join(self._text).strip()
        if tag in ("td", "th"):
            if self._where == "thead":
                if self._head_line == 0:
                    self._top.append((text, *self._span))
                else:
                    self._bottom.append(text)
            elif self._where in ("head", "tbody"):
                self._row.append(text)
            self._text = []
        elif tag == "tr":
            if self._where == "head":
                self._head_rows.append(self._row)
            elif self._where == "tbody":
                self._add_row(self._row)
            elif self._where == "thead":
                self._head_line += 1
            self._row = []
        elif tag == "div" and self._where == "stamp":
            if self._sheet is not None:
                self._sheet.stamp = text
            self._where = ""
            self._text = []
        elif tag == "table" and self._where == "head":
            self._where = ""
        elif tag == "thead":
            self._where = ""
            if self._sheet is not None:
                self._sheet.columns = self._labels()
        elif tag == "tbody" and self._where == "tbody":
            # **読むのは明細の表だけ**です。紙にはこのあと合計と
            # その直の集計が続きますが、ロットの行はここにしかありません
            self._table_done = True
            self._where = ""
        elif tag == "section" and self._sheet is not None:
            for row in self._head_rows:
                if len(row) >= 2 and row[0] == HEAD_WORKER:
                    self._sheet.worker = row[1]
            self.sheets.append(self._sheet)
            self._sheet = None

    def _labels(self) -> list[str]:
        r"""上段と下段をつないだ、**集計明細CSVと同じ見出し**。

        上段で `rowspan=2` の欄は単独(`ロット№`)。`colspan=n` の欄は
        下段から n 個を取って `梱包作業時間 開始 時` のようにつなぎます。
        **先頭の「行」は落とします**(表の1列目で、欄ではありません)。
        """
        out: list[str] = []
        at = 0
        for text, colspan, rowspan in self._top:
            # **見分けるのは `rowspan` です。** まとめの欄は1つしか
            # ぶら下がっていなくても `colspan="1"` で出るので、
            # colspan で見分けると下段と1つずつずれます(ずれました)
            if rowspan >= 2:
                out.append(text)
                continue
            for _ in range(colspan):
                out.append(f"{text} {self._bottom[at]}".strip()
                           if at < len(self._bottom) else text)
                at += 1
        return out[1:] if out and out[0] == "行" else out

    def _add_row(self, cells: list[str]) -> None:
        """表の1行。**先頭は行番号**で、そのあとが見出しと同じ並びです。"""
        if self._sheet is None or not cells:
            return
        if not any(cell for cell in cells[1:]):
            return                                # 打っていない行(紙の空欄)
        row = {"行": cells[0]}
        for name, value in zip(self._sheet.columns, cells[1:]):
            if name and name != "行":
                row[name] = value
        self._sheet.rows.append(row)


def parse(text: str) -> list[Sheet]:
    """紙のHTMLを読む(1つの窓に何ページ入っていても拾います)。"""
    reader = _Reader()
    reader.feed(text)
    reader.close()
    return reader.sheets


def read(path: Path) -> Sheet | None:
    """紙を1枚読む。読めなければ `None`(**探すのは止めません**)。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("紙を読めませんでした: %s (%s)", path, exc)
        return None
    sheets = parse(text)
    if not sheets:
        return None
    found = FILE_NAME.match(path.name)
    sheet = sheets[0]
    if found:
        # **ファイル名を信じます**(紙の頭は「2026年11月17日1直」と
        # つながっていて切り出しにくい)。日付はフォルダから入れ直します
        sheet.line = found.group("line")
        sheet.shift = found.group("shift")
        sheet.page = found.group("page")
    return sheet


def pages(base: Path, line: str, start: date, end: date) -> list[tuple]:
    r"""期間の紙を並べる。`(日付, ライン, 直, ページ, 道)`。

    **開かずに**ファイル名だけで絞れるので、集計明細にあるページは
    読まずに済みます。
    """
    root = base / line / KIND_PRINT
    out: list[tuple] = []
    if not root.is_dir():
        return out
    try:
        months = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for month in months:
        matched = plate_source.MONTH_DIR.match(month.name)
        if not matched:
            continue
        year, mon = int(matched.group(1)), int(matched.group(2))
        try:
            days = sorted(p for p in month.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in days:
            if not plate_source.DAY_DIR.match(folder.name):
                continue
            try:
                day = date(year, mon, int(folder.name))
            except ValueError:
                continue
            if not (start <= day <= end):
                continue
            try:
                files = sorted(p for p in folder.iterdir()
                               if p.is_file() and p.name.startswith("印刷_")
                               and p.suffix.lower() == ".html")
            except OSError:
                continue
            for path in files:
                found = FILE_NAME.match(path.name)
                if not found:
                    continue
                out.append((plate_source.date_text(day), found.group("line"),
                            found.group("shift"), found.group("page"), path))
    return out
