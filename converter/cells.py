"""xlsx のセルを読む ── **ふりがな(rPh)は値に混ぜません**

【何が起きていたか】
2025年のブックを機側データ集計に入れても、1枚も拾われませんでした。
対象シートの目印は `A1` が「集計」なのですが、こちらで読むと

    A1 = '集計シュウケイ'

になっていました。xlsx の文字列は

```xml
<si><t>集計</t><rPh sb="0" eb="2"><t>シュウケイ</t></rPh></si>
```

という形で、`<rPh>` が**ふりがな**です。移行元の読み手は `si` の中の
`<t>` を**全部**つないでいたので、ふりがなまで値に入っていました。

Excel の `Range("A1").Value` はふりがなを返しません。**VBA が見ている
値はあくまで「集計」**で、ツール側だけが別の文字を見ていたことになります。
見出しの確かめ(`時間（分）ジカン` など)が部分一致で通っていたため、
目印だけが完全一致で落ちていました。

【なぜ `vendor/` を直さずここに置くのか】
`vendor/` は日報管理ツールの写しで、このリポジトリでは直しません
(VENDOR.md)。**同じ直しが移行元にも要ります** ── 移行元で直して写し直す
までの間、読み手はこの1つに寄せます(2通りの読み方を作らないため)。

実物6冊で確かめたところ、ふりがなが付いているのは**見出しと項目名だけ**
(「1直操業時間(分)チョク」など29〜30件)で、**明細の値には1つも
ありません**。つまりCSVに出る数字と文字は変わりません。

【読み方そのものは写しのまま】
数式は計算済みの値を採る、エラー値(`#REF!`)は空、空白だけのセルは
無いものとして扱う ── ここは移行元と同じ約束です。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from nippou.logic.xlsx_sheet import NotXlsx  # 断り方は写しと同じものを使う

#: SpreadsheetML の名前空間
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships}")

#: ふりがな。**値ではありません**
_RUBY = NS + "rPh"
_TEXT = NS + "t"


def text_of(node) -> str:
    """文字列の中身。**`rPh`(ふりがな)の下は読みません。**

    `<si>` も `<is>`(セルに直書きされた文字列)も同じ形なので、両方これで
    読めます。`<r>`(書式で切れた断片)は中に `<t>` があるので、たどります。
    """
    found: list[str] = []
    for child in node:
        if child.tag == _RUBY:
            continue
        if child.tag == _TEXT:
            found.append(child.text or "")
        else:
            found.append(text_of(child))
    return "".join(found)


def shared_strings(book: zipfile.ZipFile) -> list[str]:
    """文字列の置き場。**無いブックもあります**(全部が数のとき)。"""
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    return [text_of(si) for si in root.findall(f"{NS}si")]


def value_of(cell, strings: list[str]) -> str:
    """セル1つの中身。数式は**計算済みの値**のほうを採ります。"""
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(f"{NS}is")
        return text_of(node) if node is not None else ""
    value = cell.find(f"{NS}v")
    if value is None or value.text is None:
        return ""
    if kind == "s":                               # 共有文字列の番号
        try:
            return strings[int(value.text)]
        except (ValueError, IndexError):
            return ""
    if kind == "e":                               # #REF! などのエラー値
        return ""
    return value.text


def cells_of(xml: bytes, strings: list[str]) -> dict[str, str]:
    """シート1枚の `{"A1": "値"}`。**中身のあるセルだけ。**"""
    root = ET.fromstring(xml)
    found: dict[str, str] = {}
    for cell in root.iter(f"{NS}c"):
        ref = cell.get("r") or ""
        text = value_of(cell, strings)
        if ref and text.strip():
            found[ref] = text.strip()
    return found


def sheet_paths(book: zipfile.ZipFile) -> list[tuple[str, str]]:
    """シートの (名前, ZIPの中の道) を**ブックの並び順**で返す。

    `sheet1.xml` と決め打ちにしません ── 並べ替えたブックでは番号と
    並び順が合わないので、関係ファイルから辿ります。
    """
    names = book.namelist()
    try:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    except KeyError as exc:                       # xlsx の形をしていない
        raise NotXlsx(f"xlsx の中身が足りません({exc})") from exc

    targets = {rel.get("Id"): rel.get("Target")
               for rel in rels.iter(f"{_REL_NS}Relationship")}
    found: list[tuple[str, str]] = []
    for sheet in workbook.iter(f"{NS}sheet"):
        target = targets.get(sheet.get(f"{_DOC_REL}id"), "")
        if not target:
            continue
        inside = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        if inside in names:
            found.append((sheet.get("name", ""), inside))
    if not found:
        raise NotXlsx("シートが1枚もありません")
    return found


def open_book(path) -> zipfile.ZipFile:
    """ZIPとして開く。開けなければ**理由を持って**断ります。"""
    try:
        return zipfile.ZipFile(Path(path))
    except zipfile.BadZipFile as exc:
        raise NotXlsx("xlsx ではありません(ZIPとして開けません)") from exc
