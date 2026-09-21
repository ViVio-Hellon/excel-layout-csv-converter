"""ブック1冊の**全シート**を読む

【なぜ別に要るのか】
既存の読み手(`nippou.logic.xlsx_sheet`)は**先頭のシート1枚**だけを読みます
── 日報1冊=1日ぶんなので、それで足りていました。ところが VBA の
「機側データ集計」は**1冊の中の全シート**を回り、`A1` が「集計」かつ
名前に `_機側` を含むものだけを拾います。同じ判定をするには、シートを
並べられる必要があります。

**値の読み方は既存のものをそのまま使います**(`_shared_strings` /
`_value_of`)。ここで独自に解釈すると、同じセルが読み手によって違う値に
なり得ます ── 数の出どころを2通りにしないのが、このリポジトリの約束です
(VENDOR.md)。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from nippou.logic import xlsx_sheet
from nippou.logic.xlsx_sheet import NotXlsx

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


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
    try:
        book = zipfile.ZipFile(Path(path))
    except zipfile.BadZipFile as exc:
        raise NotXlsx("xlsx ではありません(ZIPとして開けません)") from exc

    found: list[Sheet] = []
    with book:
        names = book.namelist()
        try:
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        except KeyError as exc:                    # xlsx の形をしていない
            raise NotXlsx(f"xlsx の中身が足りません({exc})") from exc

        strings = xlsx_sheet._shared_strings(book)     # noqa: SLF001 - 既存の読み方
        targets = {rel.get("Id"): rel.get("Target")
                   for rel in rels.iter(f"{_REL_NS}Relationship")}
        for sheet in workbook.iter(f"{_NS}sheet"):
            target = targets.get(sheet.get(f"{_DOC_REL}id"), "")
            if not target:
                continue
            inside = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
            if inside not in names:
                continue
            found.append(Sheet(name=sheet.get("name", ""),
                               cells=_cells_of(book.read(inside), strings)))
    if not found:
        raise NotXlsx("シートが1枚もありません")
    return found


def _cells_of(xml: bytes, strings: list[str]) -> dict:
    """`{"A1": "値"}`。**中身のあるセルだけ**(既存の読み手と同じ約束)。"""
    root = ET.fromstring(xml)
    found: dict[str, str] = {}
    for cell in root.iter(f"{_NS}c"):
        ref = cell.get("r") or ""
        text = xlsx_sheet._value_of(cell, strings)      # noqa: SLF001
        if ref and text.strip():
            found[ref] = text.strip()
    return found
