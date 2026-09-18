"""テスト用の xlsx を、標準ライブラリだけで組み立てる

**実物は置きません。** 作業者名も納入先も実名なので、リポジトリには
入れられません(`.gitignore`)。かわりに、同じ座標へ同じ形の値を置いた
ブックをここで作ります。

読む側の道具は書きませんが、読ませる相手が要るので、ここだけ最小の
形で組み立てます(移行元の `tests/test_nippou_sheet.py` と同じ作り)。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

from nippou.logic import xlsx_sheet              # noqa: E402

_BOOK = """<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Target="worksheets/sheet1.xml"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
</Relationships>"""


def write_xlsx(path: Path, cells: dict, name: str = "2026年8月26日_HVC") -> Path:
    """`{"A1": "値"}` から xlsx を作る。"""
    rows: dict[int, list[str]] = {}
    for ref, text in cells.items():
        _column, number = xlsx_sheet.split_ref(ref)
        safe = (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))
        rows.setdefault(number, []).append(
            f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>')
    body = "".join(f'<row r="{n}">' + "".join(rows[n]) + "</row>"
                   for n in sorted(rows))
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.'
             'openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + body + "</sheetData></worksheet>")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/workbook.xml", _BOOK.format(name=name))
        book.writestr("xl/_rels/workbook.xml.rels", _RELS)
        book.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def _row(number: int, *, lot: str, count: str, weight: str,
         stop: tuple = ("", ""), coil: bool = False) -> dict:
    """1行ぶん。**実物と同じ列**へ置きます(`docs/座標表.md`)。"""
    cells = {
        f"D{number}": lot, f"E{number}": "52S- O",
        f"F{number}": "1.500×195.0×ｺｲﾙ", f"I{number}": "12",
        f"J{number}": "08", f"K{number}": "00",
        f"L{number}": "09", f"M{number}": "00",
        f"N{number}": "1", f"P{number}": "10", f"Q{number}": "1",
        f"Y{number}": count, f"Z{number}": weight,
        f"AC{number}": "60", f"AD{number}": "100",
    }
    if stop[0]:
        cells[f"AJ{number}"] = stop[0]
        cells[f"AK{number}"] = stop[1]
    if coil:
        # コイルだけが使う欄(板では空)
        cells[f"AQ{number}"] = "2"
        cells[f"AR{number}"] = "6"
        cells[f"B{number}"] = "1"
    else:
        # 板だけが使う欄
        cells[f"O{number}"] = "有"
        cells[f"R{number}"] = "両面"
    return cells


def day_sheet(report_date: str = "2026年8月26日", line_text: str = "HVC", *,
              coil: bool = False, stops_ok: bool = True) -> dict:
    """1日ぶん(3直)。**シート自身の合計も、合うように置きます。**

    `stops_ok=False` にすると停止の合計だけを狂わせます ── 検算が
    「合わない」と言えるかを試すため。
    """
    cells = {
        "E1": report_date, "E2": line_text, "E3": "作業者A 作業者B",
    }
    # 1直: 2行(10枚 1000kg / 20枚 2000kg)、停止 0=30分
    cells["AA8"] = "1直の人"
    cells.update(_row(8, lot="A111111", count="10", weight="1000",
                      stop=("0", "30"), coil=coil))
    cells.update(_row(9, lot="A222222", count="20", weight="2000", coil=coil))
    cells["AA55"], cells["AB55"] = "30", "3000"
    # 2直: 1行、停止 タ=20分
    cells["AA56"] = "2直の人"
    cells.update(_row(56, lot="B111111", count="5", weight="500.5",
                      stop=("タ", "20"), coil=coil))
    cells["AA103"], cells["AB103"] = "5", "500.5"
    # 3直: 1行、停止 G=10分
    cells["AA104"] = "3直の人"
    cells.update(_row(104, lot="C111111", count="7", weight="700",
                      stop=("G", "10"), coil=coil))
    cells["AA151"], cells["AB151"] = "7", "700"
    # 日計と停止3分類
    cells["AA3"], cells["AB3"] = "42", "4200.5"
    cells["T4"] = "30" if stops_ok else "999"    # 管理ロス(0)
    cells["V4"] = "20"                            # 突発(タ)
    cells["X4"] = "10"                            # ハンドリング(G)
    return cells
