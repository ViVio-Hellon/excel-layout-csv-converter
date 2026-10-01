"""ブック1冊の**全シート**を読む

【なぜ別に要るのか】
既存の読み手(`nippou.logic.xlsx_sheet`)は**先頭のシート1枚**だけを読みます
── 日報1冊=1日ぶんなので、それで足りていました。ところが VBA の
「機側データ集計」は**1冊の中の全シート**を回り、`A1` が「集計」かつ
名前に `_機側` を含むものだけを拾います。同じ判定をするには、シートを
並べられる必要があります。

**値の読み方は1つに寄せます**(`converter/cells.py`)。同じセルが読み手に
よって違う値になるのを避けるためで、変換側(先頭シート)もここを通します。
`cells.py` は移行元の読み方そのままですが、**ふりがな(rPh)を値に混ぜない**
一点だけ直してあります(理由は `cells.py` の頭に書いてあります)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nippou.logic import xlsx_sheet

from . import cells
from .cells import NotXlsx

__all__ = ["NotXlsx", "Sheet", "first_sheet", "read_sheets"]


@dataclass
class Sheet:
    """シート1枚。`rows` は `{行番号: {"D": "値"}}`。"""

    name: str = ""
    cells: dict = field(default_factory=dict)

    @property
    def rows(self) -> dict:
        return xlsx_sheet.rows_of(self.cells)

    def cell(self, ref: str) -> str:
        return self.cells.get(ref, "")


def read_sheets(path) -> list[Sheet]:
    """ブックの中の全シートを、**ブックに並んでいる順**で返す。

    並び順を変えません ── VBA の `For Each ws In wb.Worksheets` と同じ順で
    拾わないと、同じ行が違う順番で積まれます(転記は順番で結果が変わる)。
    """
    found: list[Sheet] = []
    with cells.open_book(Path(path)) as book:
        strings = cells.shared_strings(book)
        for name, inside in cells.sheet_paths(book):
            found.append(Sheet(name=name,
                               cells=cells.cells_of(book.read(inside), strings)))
    return found


def first_sheet(path) -> Sheet:
    """先頭のシート1枚。**1冊につき1回しか開きません。**

    変換(`converter/run.py`)はここを通ります ── 名前と中身を別々に取ると
    同じブックを2回開くことになり、共有フォルダでは往復が効きます。
    """
    with cells.open_book(Path(path)) as book:
        strings = cells.shared_strings(book)
        name, inside = cells.sheet_paths(book)[0]
        return Sheet(name=name, cells=cells.cells_of(book.read(inside), strings))
