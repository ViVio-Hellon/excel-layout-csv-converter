r"""Excel(.xlsx)を**追加ライブラリ無しで**書く ── 本物のグラフまで

【なぜ自前で書くのか】
ラインの端末には追加ライブラリを入れられません(openpyxl も入れません)。
`.xlsx` は **zip の中に XML が入っているだけ**なので、標準ライブラリの
`zipfile` と文字列で組み立てられます。

**グラフを画像で貼りません。** 画像にすると、受け取った人が数字を直しても
絵が変わらず、軸や凡例も直せません ── Excel のグラフとして入れておけば、
あとは Excel の仕事になります(系列の追加・期間の絞り込み・印刷)。

【入れるもの】
    xl/worksheets/sheet1.xml …  表(まとめた集計・停止内訳)
    xl/charts/chart1.xml …      グラフ(棒・折れ線)。表のセルを指します
    xl/drawings/drawing1.xml    グラフを「グラフ」シートのどこに置くか

グラフは**表のセルを参照**します(`'集計'!$E$2:$E$32`)。値を写さないので、
表を直すとグラフが追います ── Excel で開いたときの当たり前の挙動です。

【書かないもの】
色・フォント・罫線はほとんど指定しません。既定のままにしておくと、
受け取った端末の Excel の見た目に馴染みます(濃い色を入れると、印刷で
真っ黒になる端末があります)。見出しの太字と、日付・数の表示形式だけ
入れます ── そこだけは「読めない」に直結します。
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: 1シートに入れる行の上限。Excel の上限は 1,048,576 行ですが、
#: **そこまで入れる前に人が読めなくなります**。超えたら切って、
#: 切ったことをシートに書きます(黙って落としません)
MAX_ROWS = 200_000

#: グラフ1枚の大きさ(EMU。1cm = 360000)と、縦に並べる間隔
CHART_W = 16 * 360_000
CHART_H = 9 * 360_000
CHART_ROWS = 20                        # 1枚ぶんの行数(置き場所の目安)


def escape(text: str) -> str:
    """XML に入れられる字へ。**制御文字は落とします**(Excel が開けなくなる)。"""
    out = (str(text).replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace('"', "&quot;"))
    return "".join(ch for ch in out
                   if ch >= " " or ch in "\t\n")


def column_letter(index: int) -> str:
    """0 → A、25 → Z、26 → AA。"""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def sheet_ref(name: str, column: int, row_from: int, row_to: int) -> str:
    """`'集計'!$E$2:$E$32`。**シート名は必ず引用符で囲みます** ──
    日本語やハイフンが入っていても Excel が読めるように。"""
    letter = column_letter(column)
    quoted = name.replace("'", "''")
    return f"'{quoted}'!${letter}${row_from}:${letter}${row_to}"


# ----------------------------------------------------------------------
# 入れるもの
# ----------------------------------------------------------------------
@dataclass
class Sheet:
    """表1枚。`rows` の中身は数か字で、**数は数のセルにします**。

    数を字で入れると、Excel で合計も並べ替えもできず、グラフも描けません
    (「数値が文字列として保存されています」の三角が全セルに出ます)。
    """

    name: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    #: 列ごとの幅(字数)。**パスや内訳名が切れないように**
    widths: dict[int, int] = field(default_factory=dict)
    #: 先頭に出す覚え書き(期間・ライン・読めなかったもの)
    notes: list[str] = field(default_factory=list)

    @property
    def header_row(self) -> int:
        """見出しの行番号(1から数えます)。覚え書きのぶん下がります。"""
        return len(self.notes) + (2 if self.notes else 1)

    @property
    def first_data_row(self) -> int:
        return self.header_row + 1

    @property
    def last_data_row(self) -> int:
        return self.header_row + min(len(self.rows), MAX_ROWS)


@dataclass
class Series:
    """グラフの1本。`column` は表の何列目か(0から)。"""

    title: str
    column: int
    kind: str = "line"                 # bar / line


@dataclass
class Chart:
    """グラフ1枚。**表のどの列を、どの軸で見るか**だけを持ちます。"""

    title: str
    source: str                        # どのシートの表を見るか(シート名)
    labels: int                        # 横軸に使う列(0から)
    series: list[Series] = field(default_factory=list)
    kind: str = "line"                 # bar / line(系列ごとにも指定できます)
    y_title: str = ""


# ----------------------------------------------------------------------
# 組み立て
# ----------------------------------------------------------------------
def _cell(ref: str, value: object, style: int = 0) -> str:
    """1セル。数は `<v>`、字は `<is>`(共有文字列を使わない形)。"""
    attr = f' s="{style}"' if style else ""
    if isinstance(value, bool) or value is None or value == "":
        return f'<c r="{ref}"{attr}/>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{attr}><v>{value}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"{attr}>'
            f"<is><t>{escape(str(value))}</t></is></c>")


def _sheet_xml(sheet: Sheet) -> str:
    """1シートぶんの XML。見出しは太字(`s="1"`)。"""
    lines: list[str] = []
    row_no = 1
    for note in sheet.notes:
        lines.append(f'<row r="{row_no}">{_cell(f"A{row_no}", note)}</row>')
        row_no += 1
    if sheet.notes:
        row_no += 1                     # 覚え書きと表のあいだを1行空ける

    head = "".join(_cell(f"{column_letter(i)}{row_no}", name, 1)
                   for i, name in enumerate(sheet.columns))
    lines.append(f'<row r="{row_no}">{head}</row>')

    for row in sheet.rows[:MAX_ROWS]:
        row_no += 1
        cells = "".join(_cell(f"{column_letter(i)}{row_no}", value)
                        for i, value in enumerate(row))
        lines.append(f'<row r="{row_no}">{cells}</row>')
    if len(sheet.rows) > MAX_ROWS:
        row_no += 2
        lines.append(f'<row r="{row_no}">'
                     + _cell(f"A{row_no}",
                             f"※ ここから下は入れていません"
                             f"(全 {len(sheet.rows):,} 行のうち {MAX_ROWS:,} 行まで)")
                     + "</row>")

    cols = ""
    if sheet.widths:
        parts = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in sorted(sheet.widths.items()))
        cols = f"<cols>{parts}</cols>"

    # 見出しの行で**固定**します(下へたどっても列の名前が見える)
    freeze = (f'<sheetViews><sheetView workbookViewId="0">'
              f'<pane ySplit="{sheet.header_row}" topLeftCell="A{sheet.header_row + 1}"'
              f' activePane="bottomLeft" state="frozen"/>'
              f"</sheetView></sheetViews>")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            + freeze + cols
            + "<sheetData>" + "".join(lines) + "</sheetData></worksheet>")


def _chart_sheet_xml(count: int) -> str:
    """グラフを置くシート。**表は入れません**(絵だけのシート)。"""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheetData/>"
            + ('<drawing r:id="rId1"/>' if count else "")
            + "</worksheet>")


def _series_xml(index: int, chart: Chart, series: Series, sheet: Sheet) -> str:
    """系列1本。**値はセルの参照**(写した数ではありません)。"""
    cats = sheet_ref(sheet.name, chart.labels, sheet.first_data_row,
                     sheet.last_data_row)
    vals = sheet_ref(sheet.name, series.column, sheet.first_data_row,
                     sheet.last_data_row)
    name = sheet_ref(sheet.name, series.column, sheet.header_row,
                     sheet.header_row)
    return (f"<c:ser><c:idx val=\"{index}\"/><c:order val=\"{index}\"/>"
            f"<c:tx><c:strRef><c:f>{escape(name)}</c:f></c:strRef></c:tx>"
            + ("<c:marker><c:symbol val=\"circle\"/><c:size val=\"5\"/></c:marker>"
               if series.kind == "line" else "")
            + f"<c:cat><c:strRef><c:f>{escape(cats)}</c:f></c:strRef></c:cat>"
            f"<c:val><c:numRef><c:f>{escape(vals)}</c:f></c:numRef></c:val>"
            + ("<c:smooth val=\"0\"/>" if series.kind == "line" else "")
            + "</c:ser>")


def _chart_xml(chart: Chart, sheet: Sheet) -> str:
    r"""グラフ1枚ぶんの XML。

    **棒と折れ線を1枚に混ぜられます** ── 系列ごとに `kind` を見て、
    `barChart` と `lineChart` を同じ軸の上に並べます(枚数の棒と
    累積の折れ線のような組み合わせ)。軸は共通です ── 左右に分けると
    Excel 側で軸を直すのが難しくなるので、**分けるならシートを分ける**
    ほうを選びました。
    """
    bars = [s for s in chart.series if (s.kind or chart.kind) == "bar"]
    liner = [s for s in chart.series if (s.kind or chart.kind) == "line"]
    plots = ""
    index = 0
    if bars:
        body = "".join(_series_xml(index + i, chart, s, sheet)
                       for i, s in enumerate(bars))
        index += len(bars)
        plots += ('<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
                  '<c:varyColors val="0"/>' + body
                  + '<c:gapWidth val="60"/>'
                  '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>')
    if liner:
        body = "".join(_series_xml(index + i, chart, s, sheet)
                       for i, s in enumerate(liner))
        plots += ('<c:lineChart><c:grouping val="standard"/>'
                  '<c:varyColors val="0"/>' + body
                  + '<c:marker val="1"/>'
                  '<c:axId val="111111111"/><c:axId val="222222222"/></c:lineChart>')

    title = (f'<c:title><c:tx><c:rich>'
             f'<a:bodyPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
             f'<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
             f'<a:r><a:t>{escape(chart.title)}</a:t></a:r></a:p>'
             f'</c:rich></c:tx><c:overlay val="0"/></c:title>'
             '<c:autoTitleDeleted val="0"/>')
    y_title = ""
    if chart.y_title:
        y_title = ('<c:title><c:tx><c:rich>'
                   '<a:bodyPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
                   ' rot="-5400000" vert="horz"/>'
                   '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                   f'<a:r><a:t>{escape(chart.y_title)}</a:t></a:r></a:p>'
                   '</c:rich></c:tx><c:overlay val="0"/></c:title>')
    axes = ('<c:catAx><c:axId val="111111111"/><c:scaling>'
            '<c:orientation val="minMax"/></c:scaling><c:delete val="0"/>'
            '<c:axPos val="b"/><c:crossAx val="222222222"/></c:catAx>'
            '<c:valAx><c:axId val="222222222"/><c:scaling>'
            '<c:orientation val="minMax"/></c:scaling><c:delete val="0"/>'
            # **並びは決まっています**(目盛り線 → 軸の名前)。入れ替えると
            # Excel が「読み取れない内容」と言って開きません
            '<c:axPos val="l"/><c:majorGridlines/>' + y_title
            + '<c:crossAx val="111111111"/></c:valAx>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<c:chartSpace'
            ' xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<c:chart>" + title + "<c:plotArea><c:layout/>" + plots + axes
            + "</c:plotArea>"
            '<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
            '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
            "</c:chart></c:chartSpace>")


def _drawing_xml(count: int) -> str:
    """グラフの置き場所。**縦に並べます**(横に並べると印刷で切れます)。"""
    parts = []
    for i in range(count):
        top = i * CHART_ROWS
        parts.append(
            '<xdr:twoCellAnchor editAs="oneCell">'
            f'<xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff>'
            f'<xdr:row>{top}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
            f'<xdr:to><xdr:col>10</xdr:col><xdr:colOff>0</xdr:colOff>'
            f'<xdr:row>{top + CHART_ROWS - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
            "<xdr:graphicFrame><xdr:nvGraphicFramePr>"
            f'<xdr:cNvPr id="{i + 2}" name="グラフ {i + 1}"/>'
            "<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>"
            '<xdr:xfrm><a:off x="0" y="0"/>'
            f'<a:ext cx="{CHART_W}" cy="{CHART_H}"/></xdr:xfrm>'
            '<a:graphic><a:graphicData'
            ' uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f'<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
            f' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            f' r:id="rId{i + 1}"/>'
            "</a:graphicData></a:graphic></xdr:graphicFrame>"
            "<xdr:clientData/></xdr:twoCellAnchor>")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr'
            ' xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
            ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            + "".join(parts) + "</xdr:wsDr>")


#: 見出しの太字だけ入れた最小の書式
_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="2"><font><sz val="11"/><name val="Yu Gothic"/></font>'
           '<font><b/><sz val="11"/><name val="Yu Gothic"/></font></fonts>'
           '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
           '<borders count="1"><border/></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0"/></cellStyleXfs>'
           '<cellXfs count="2">'
           '<xf numFmtId="0" fontId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" xfId="0" applyFont="1"/>'
           "</cellXfs></styleSheet>")

#: グラフを置くシートの名前
CHART_SHEET = "グラフ"


def write(path: Path, sheets: list[Sheet], charts: list[Chart]) -> Path:
    r"""`.xlsx` を1つ書く。**グラフは先頭のシート**に置きます。

    グラフを先に置くのは、開いたときに**絵から入れる**ためです ──
    数を確かめたい人は隣のシートへ移ります(逆だと、絵があることに
    気づかないまま閉じられます)。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    by_name = {sheet.name: sheet for sheet in sheets}
    charts = [c for c in charts if c.source in by_name and c.series]

    order: list[tuple[str, str]] = []            # (シート名, 中の XML)
    if charts:
        order.append((CHART_SHEET, _chart_sheet_xml(len(charts))))
    for sheet in sheets:
        order.append((sheet.name, _sheet_xml(sheet)))

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # ---- どのファイルが何か ----
        overrides = [
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        for i in range(len(order)):
            overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        for i in range(len(charts)):
            overrides.append(
                f'<Override PartName="/xl/charts/chart{i + 1}.xml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>')
        if charts:
            overrides.append(
                '<Override PartName="/xl/drawings/drawing1.xml"'
                ' ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>')
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    + "".join(overrides) + "</Types>")

        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1"'
                    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
                    ' Target="xl/workbook.xml"/></Relationships>')

        # ---- 本(シートの並び) ----
        tabs = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, (name, _) in enumerate(order))
        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f"<sheets>{tabs}</sheets></workbook>")

        rels = [f'<Relationship Id="rId{i + 1}"'
                ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
                f' Target="worksheets/sheet{i + 1}.xml"/>'
                for i in range(len(order))]
        rels.append(f'<Relationship Id="rId{len(order) + 1}"'
                    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
                    ' Target="styles.xml"/>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(rels) + "</Relationships>")
        zf.writestr("xl/styles.xml", _STYLES)

        for i, (_, body) in enumerate(order):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", body)

        if charts:
            # グラフのシート(1枚目)から絵へ
            zf.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1"'
                        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"'
                        ' Target="../drawings/drawing1.xml"/></Relationships>')
            zf.writestr("xl/drawings/drawing1.xml", _drawing_xml(len(charts)))
            chart_rels = "".join(
                f'<Relationship Id="rId{i + 1}"'
                ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"'
                f' Target="../charts/chart{i + 1}.xml"/>'
                for i in range(len(charts)))
            zf.writestr("xl/drawings/_rels/drawing1.xml.rels",
                        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        + chart_rels + "</Relationships>")
            for i, chart in enumerate(charts):
                zf.writestr(f"xl/charts/chart{i + 1}.xml",
                            _chart_xml(chart, by_name[chart.source]))
    return path
