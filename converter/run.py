"""xlsx を読んで、日報管理ツールと**同じCSV**を書く

【なぜ使い捨ての SQLite を挟むのか】
集計CSVの出どころは**集計テーブル**です(日報管理ツールの
`services/summary.day_rows`)。流れはこうなっています:

    xlsx → 日報の明細 → 保存 → 集計を作り直す → CSV

ここで「自前で数え直す」ことをすると、**ツールと数字がずれる余地**が
できます。同じ数を2通りの道で出すことになるからです。

なので使い捨ての SQLite を1つ作って、**ツールと同じ関数をそのまま
通し**、終わったらDBごと捨てます。画面もサーバも要らず、残るのはCSV
だけ。一致は作り込むものではなく、同じコードを通した結果です。

【ラインの決め方】
シートには `機側` `HVC` のような現場の呼び名が入っています。ツールの
ライン名(`LS` `HVC` …)とは違うので、`guess_line` が当たりを付けます。
外れることがあるので **`--line` で上書きできます** ── 黙って当てるより、
当てた結果を出して人が見られるほうがよい。
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import check

#: 読む拡張子。`.xls`(古い形式)は ZIP ではないので読めません
SUFFIXES = (".xlsx", ".xlsm")


@dataclass
class FileResult:
    """1ファイルぶんの結果。"""

    path: Path
    name: str = ""
    report_date: str = ""
    line_text: str = ""       # シートに書いてある呼び名
    line: str = ""            # 入れた先のライン
    pages: int = 0
    rows: int = 0
    error: str = ""
    verify: check.Report = field(default_factory=check.Report)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class RunResult:
    files: list[FileResult] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)

    @property
    def read_ok(self) -> list[FileResult]:
        return [f for f in self.files if f.ok]

    @property
    def verified(self) -> list[FileResult]:
        return [f for f in self.read_ok if f.verify.ok]

    @property
    def suspect(self) -> list[FileResult]:
        """読めたが、数がシートと合わなかったもの。**黙って通さない。**"""
        return [f for f in self.read_ok if not f.verify.ok]


def gather(inputs) -> list[Path]:
    """渡されたファイル/フォルダから、読む相手を集める。**名前順。**"""
    found: list[Path] = []
    for one in inputs:
        path = Path(one)
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*"))
                         if p.suffix.lower() in SUFFIXES and not p.name.startswith("~$"))
        elif path.suffix.lower() in SUFFIXES:
            found.append(path)
    return found


def _totals_of(pages) -> tuple[dict, float]:
    """読んだ明細を、直ごとの(枚数, 重量)と停止の合計に畳む。"""
    by_shift: dict[str, tuple[float, float]] = {}
    stops = 0.0
    for page in pages:
        shift = page.header.shift
        count, weight = by_shift.get(shift, (0.0, 0.0))
        for row in page.details:
            count += check.to_number(row.con)
            weight += check.to_number(row.wei)
            for code, minutes in ((row.s, row.th), (row.ss, row.ths),
                                  (row.sth, row.tht)):
                if str(code).strip():
                    stops += check.to_number(minutes)
        by_shift[shift] = (count, weight)
    return by_shift, stops


def convert(inputs, out_dir, *, line: str = "", dry_run: bool = False,
            on_file=None) -> RunResult:
    """変換する。`line` を渡すと、シートの呼び名より優先します。

    `on_file` は1本終わるたびに呼ばれます(進み具合を出すため)。
    `dry_run` なら**CSVを1本も書きません** ── 読めるか・数が合うかだけ見ます。
    """
    from nippou.db import connection
    from nippou.db.repository import NippouRepository
    from nippou.logic import nippou_sheet, xlsx_sheet
    from nippou.reporting import csv_export
    from nippou.services import summary

    out_dir = Path(out_dir)
    result = RunResult()
    books = gather(inputs)

    with tempfile.TemporaryDirectory(prefix="convert-") as work:
        # **使い捨て。** 表はここで作られ、抜けるときに消えます
        conn = connection.connect(Path(work) / "convert.sqlite3")
        repo = NippouRepository(conn)
        days: set[tuple[str, str]] = set()

        for book in books:
            one = FileResult(path=book, name=book.name)
            try:
                head = nippou_sheet.read_file_head(book)
                one.report_date = head.report_date
                one.line_text = head.line_text
                one.line = line or nippou_sheet.guess_line(head.line_text)
                if not one.line:
                    raise ValueError(
                        f"ラインが決まりません(シートの呼び名: "
                        f"{head.line_text or '(空)'})。--line で指定してください")

                parsed = nippou_sheet.parse_file(book, one.line)
                if parsed.problems:
                    raise ValueError("／".join(p.message for p in parsed.problems))

                one.pages = len(parsed.pages)
                one.rows = sum(len(p.details) for p in parsed.pages)

                # **座標が合っていたかを、ここで確かめます**
                rows = xlsx_sheet.rows_of(xlsx_sheet.read_cells(book))
                sheet = nippou_sheet.read_totals(rows)
                read_by_shift, read_stops = _totals_of(parsed.pages)
                one.verify = check.build(
                    book.name,
                    read_by_shift=read_by_shift, read_stops=read_stops,
                    sheet_by_shift=sheet.by_shift,
                    sheet_day=(sheet.day_count, sheet.day_weight),
                    sheet_stops=sheet.stops)

                for page in parsed.pages:
                    repo.save(page.header, page.details)
                    days.add((page.header.report_date, page.header.line))
            except Exception as exc:              # noqa: BLE001 - 残りは続ける
                one.error = str(exc)
            result.files.append(one)
            if on_file:
                on_file(one, len(result.files), len(books))

        if not dry_run:
            for report_date, ln in sorted(days):
                for shift in ("1直", "2直", "3直", "日勤"):
                    try:
                        summary.refresh_shift(repo, report_date, ln, shift)
                    except Exception:             # その直が無ければ素通り
                        pass
                found = csv_export.write_daily_set(repo, report_date, ln, out_dir)
                result.written.extend(path for path, _n in found.files.values())
        conn.close()
    return result
