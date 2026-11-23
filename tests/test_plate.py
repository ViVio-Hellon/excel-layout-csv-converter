"""板集計 ── 日ごとのCSVを期間ぶん1つにまとめ、グラフにして書き出す

見ているのは4つです。

    読み取り  期間で絞れる・読めなかったものを黙って捨てない・列が
              増えても位置でずれない
    グラフ    稼働率を**出し直す**(直の平均にしない)・輪は長い順
    書き出し  CSVは1本・Excelは**本物のグラフ**が表のセルを指している
    経路      ラインは一覧にあるものだけ受ける(参照パスの外を読ませない)
"""
from __future__ import annotations

import csv
import datetime
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter import (plate_graph, plate_out, plate_paper,  # noqa: E402
                       plate_source, user_settings, xlsx_out)
from tests._paper_sample import SAMPLE                       # noqa: E402
from tests._web import WebCase                               # noqa: E402

DAY1 = datetime.date(2026, 11, 1)
DAY30 = datetime.date(2026, 11, 30)


#: 集計明細CSVの列(`nippou.reporting.csv_export.DETAIL_FIELDNAMES` の形)。
#: ロットを探すが読むのはこちらです ── 1行=1ロット
DETAIL_COLUMNS = (
    "日付", "ライン", "直", "ページ", "行", "ロット№", "材・調質",
    "製品寸法 厚×幅×丈", "検入枚数",
    "梱包作業時間 開始 時", "梱包作業時間 開始 分",
    "梱包作業時間 終了 時", "梱包作業時間 終了 分",
    "実績合計 枚数", "実績合計 重量", "作業時間(分)",
    "作業者", "用途名", "納入先",
)


def write_detail(folder: Path, line: str, stamp: str, lots) -> Path:
    """その日の集計明細CSVを置く。`lots` は `(直, ページ, 行, ロット№)`。"""
    path = folder / f"集計明細_{stamp}_{line}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DETAIL_COLUMNS))
        writer.writeheader()
        for shift, page, row_no, lot in lots:
            writer.writerow({
                **{name: "" for name in DETAIL_COLUMNS},
                "日付": stamp, "ライン": line, "直": shift, "ページ": page,
                "行": row_no, "ロット№": lot, "材・調質": "SS400",
                "製品寸法 厚×幅×丈": "3.2×1219×2438", "検入枚数": 40,
                "梱包作業時間 開始 時": "08", "梱包作業時間 開始 分": "15",
                "梱包作業時間 終了 時": "09", "梱包作業時間 終了 分": "40",
                "実績合計 枚数": 40, "実績合計 重量": "2.4",
                "作業時間(分)": 85, "作業者": "山田", "用途名": "外販",
                "納入先": "〇〇鋼材",
            })
    return path


def write_day(base: Path, line: str, stamp: str, *, shifts=("1直", "2直", "3直"),
              extra: dict | None = None, columns=None, stops=True,
              lots=None) -> Path:
    """日報管理ツールが書く形で、1日ぶん置く。"""
    date_part = stamp.split("-")
    folder = (base / line / "集計" / f"{date_part[0]}.{date_part[1]}"
              / date_part[2])
    folder.mkdir(parents=True, exist_ok=True)
    cols = list(columns or plate_source.AGGREGATE_COLUMNS)
    with (folder / f"集計_{stamp}_{line}.csv").open(
            "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols)
        writer.writeheader()
        for index, shift in enumerate(shifts):
            row = {name: "" for name in cols}
            row.update({"日付": stamp, "ライン": line, "直": shift,
                        "合計枚数": 100 + index, "合計重量(t)": "12.50",
                        "停止合計(分)": 60, "管理ロス設備停止(分)": 30,
                        "段取り・突発停止(分)": 20, "ハンドリング停止(分)": 10,
                        "稼働率(%)": "95.5", "生産性(t/h)": "3.21"})
            row.update(extra or {})
            writer.writerow({k: v for k, v in row.items() if k in cols})
    if stops:
        stop_cols = list(plate_source.STOP_COLUMNS)
        with (folder / f"停止内訳_{stamp}_{line}.csv").open(
                "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=stop_cols)
            writer.writeheader()
            for name, minutes in (("休憩食事", 60), ("ﾌｫｰｸ待ち", 25)):
                row = {name_: "" for name_ in stop_cols}
                row.update({"日付": stamp, "ライン": line, "直": "1直",
                            "区分": "直の合計", "分類": "管理ロス停止",
                            "記号": "0", "内訳": name, "回数": 1,
                            "停止時間(分)": minutes})
                writer.writerow(row)
                # **1件ずつの行も置きます。** 足すと二重に数えるので、
                # 集計が「直の合計」だけを拾っていることを確かめます
                one = dict(row)
                one.update({"区分": "1件ずつ", "ページ": 1, "行": 2,
                            "停止時間(分)": minutes})
                writer.writerow(one)
    if lots:
        write_detail(folder, line, stamp, lots)
    return folder


class SourceTests(WebCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()

    # -- 参照パスを見る ----------------------------------------------
    def test_ラインの一覧は集計フォルダで見分ける(self) -> None:
        write_day(self.base, "L-1", "2026-11-17")
        (self.base / "L-2" / "印刷").mkdir(parents=True)   # 紙だけのライン
        (self.base / "配布設定").mkdir()                    # ラインではない持ちもの
        found = {one.name: one for one in plate_source.lines(self.base)}
        self.assertTrue(found["L-1"].has_aggregate)
        self.assertEqual(found["L-1"].days, 1)
        self.assertEqual(found["L-1"].first, "2026-11-17")
        # **消しません。** 無いものも出して「集計がありません」と言います
        self.assertFalse(found["L-2"].has_aggregate)
        self.assertFalse(found["配布設定"].has_aggregate)

    def test_日フォルダは日付順で_読めない名前は飛ばす(self) -> None:
        for stamp in ("2026-11-02", "2026-10-30", "2026-11-10"):
            write_day(self.base, "L-1", stamp)
        (self.base / "L-1" / "集計" / "2026.11" / "99").mkdir()   # 11月99日
        (self.base / "L-1" / "集計" / "まとめ").mkdir()
        got = [plate_source.date_text(d)
               for d, _ in plate_source.days(self.base, "L-1")]
        self.assertEqual(got, ["2026-10-30", "2026-11-02", "2026-11-10"])

    def test_参照パスが開けなくても落ちない(self) -> None:
        self.assertEqual(plate_source.lines(self.base / "まだ無い"), [])

    # -- まとめる ----------------------------------------------------
    def test_期間で絞って縦に連結する(self) -> None:
        for day in (1, 15, 30):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        write_day(self.base, "L-1", "2026-12-01")
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertEqual(len(got.rows), 9)            # 3日 × 3直
        self.assertEqual(got.dates, ["2026-11-01", "2026-11-15", "2026-11-30"])
        self.assertEqual(got.lines, ["L-1"])
        self.assertEqual(got.rows[0]["日付"], "2026-11-01")
        self.assertEqual(got.columns[:3], ["日付", "ライン", "直"])

    def test_ラインが複数なら日付順に混ぜる(self) -> None:
        write_day(self.base, "L-1", "2026-11-02")
        write_day(self.base, "L-2", "2026-11-01")
        got = plate_source.collect(self.base, ["L-1", "L-2"], DAY1, DAY30)
        self.assertEqual([(r["日付"], r["ライン"]) for r in got.rows][:4],
                         [("2026-11-01", "L-2")] * 3 + [("2026-11-02", "L-1")])

    def test_列が増えても位置でずれない(self) -> None:
        """all-tools は列を足します(`その他(分)`)。**名前で読みます。**"""
        columns = list(plate_source.AGGREGATE_COLUMNS) + ["あとで足した欄"]
        write_day(self.base, "L-1", "2026-11-05", columns=columns,
                  extra={"その他(分)": 7, "あとで足した欄": "なにか"})
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertEqual(got.rows[0]["その他(分)"], "7")
        self.assertEqual(got.rows[0]["あとで足した欄"], "なにか")
        # 知らない列は**後ろへ**(既知の列の位置は動かしません)
        self.assertEqual(got.columns[-1], "あとで足した欄")
        self.assertEqual(got.columns[11], "その他(分)")

    def test_その他の欄が無い古いCSVも読める(self) -> None:
        columns = [c for c in plate_source.AGGREGATE_COLUMNS
                   if c != "その他(分)"]
        write_day(self.base, "L-1", "2026-11-05", columns=columns)
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertNotIn("その他(分)", got.columns)
        self.assertEqual(len(got.rows), 3)

    def test_フォルダの日付で足りない欄を埋める(self) -> None:
        folder = write_day(self.base, "L-1", "2026-11-07")
        path = next(folder.glob("集計_*.csv"))
        text = path.read_text(encoding="utf-8-sig").replace("2026-11-07", "")
        path.write_text(text, encoding="utf-8-sig")
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertEqual(got.rows[0]["日付"], "2026-11-07")

    # -- 読めなかったものを黙って捨てない ----------------------------
    def test_集計CSVが無い日は理由を残す(self) -> None:
        folder = write_day(self.base, "L-1", "2026-11-08")
        next(folder.glob("集計_*.csv")).unlink()
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertTrue(got.empty)
        self.assertIn("集計CSVがありません", got.skipped[0]["reason"])
        self.assertEqual(got.skipped[0]["date"], "2026-11-08")

    def test_見出しが違うCSVは読まない(self) -> None:
        folder = write_day(self.base, "L-1", "2026-11-09")
        path = next(folder.glob("集計_*.csv"))
        path.write_text("よその,見出し\n1,2\n", encoding="utf-8-sig")
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertTrue(got.empty)
        self.assertIn("見出しが違います", got.skipped[0]["reason"])

    def test_その期間に日フォルダが無ければ言う(self) -> None:
        write_day(self.base, "L-1", "2026-11-09")
        got = plate_source.collect(self.base, ["L-1"],
                                   datetime.date(2026, 1, 1),
                                   datetime.date(2026, 1, 31))
        self.assertIn("日フォルダがありません", got.skipped[0]["reason"])

    def test_期間が逆でも受ける(self) -> None:
        write_day(self.base, "L-1", "2026-11-09")
        got = plate_source.collect(self.base, ["L-1"], DAY30, DAY1)
        self.assertEqual(len(got.rows), 3)

    def test_広すぎる期間は断る(self) -> None:
        original = plate_source.MAX_DAYS
        plate_source.MAX_DAYS = 2
        self.addCleanup(setattr, plate_source, "MAX_DAYS", original)
        for day in (1, 2, 3):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        with self.assertRaises(ValueError) as caught:
            plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.assertIn("期間が広すぎます", str(caught.exception))

    def test_日付の書き方は3通り受ける(self) -> None:
        for text in ("2026-11-17", "2026/11/17", "2026.11.17"):
            self.assertEqual(plate_source.parse_date(text),
                             datetime.date(2026, 11, 17))
        self.assertIsNone(plate_source.parse_date("17日"))

    def test_数にできない欄は0として足す(self) -> None:
        self.assertEqual(plate_source.number("1,234"), 1234)
        self.assertEqual(plate_source.number(""), 0)
        self.assertEqual(plate_source.number("―"), 0)


class GraphTests(WebCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        for day in (1, 2, 3):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        self.got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)

    def tiles(self, collected=None) -> dict:
        board = plate_graph.build(collected or self.got, note="試験")
        return {tile["key"]: tile for tile in board["tiles"]}

    def test_並べるタイルが全部ある(self) -> None:
        keys = self.tiles().keys()
        for key in ("weight", "sheets", "rate", "stop", "trend_weight",
                    "trend_rate", "trend_sheets", "trend_cumulative",
                    "shift_weight", "shift_stop", "stop_kinds", "stop_items",
                    "day_table", "stop_table"):
            self.assertIn(key, keys)

    def test_稼働率は直の平均ではなく出し直す(self) -> None:
        """1直だけ動いた日と3直とも動いた日が同じ数字になりません。"""
        rows = plate_graph.shift_rows(self.got.rows)
        one = [r for r in rows if r.date == "2026-11-01"]
        self.assertEqual(len(one), 3)
        # 3直 × 1440分 − 停止 180分
        expected = (1440 * 3 - 180) / (1440 * 3) * 100
        self.assertAlmostEqual(plate_graph.day_rate_pct(one), expected, places=4)
        # CSVの「稼働率(%)」(95.5)をそのまま平均したものではない
        self.assertNotAlmostEqual(plate_graph.day_rate_pct(one), 95.5, places=1)

    def test_累積は右の目盛りで重ねる(self) -> None:
        tile = self.tiles()["trend_cumulative"]
        bars, line = tile["series"]
        self.assertEqual(bars["axis"], "left")
        self.assertEqual(line["axis"], "right")
        self.assertEqual(line["values"][-1], sum(bars["values"]))

    def test_直別の棒は直の色で塗る(self) -> None:
        tile = self.tiles()["shift_weight"]
        self.assertEqual(tile["labels"], ["1直", "2直", "3直"])
        self.assertEqual(tile["point_classes"],
                         ["shift-1", "shift-2", "shift-3"])

    def test_停止の輪は長い順で_1件ずつは足さない(self) -> None:
        tile = self.tiles()["stop_items"]
        labels = [s["label"] for s in tile["slices"]]
        self.assertEqual(labels, ["休憩食事", "ﾌｫｰｸ待ち"])
        # 3日 × 60分。1件ずつの行を足すと2倍になります
        self.assertEqual(tile["slices"][0]["value"], 180)

    def test_項目が多いときはまとめる(self) -> None:
        stops = [{"区分": "直の合計", "内訳": f"項目{i}", "停止時間(分)": 100 - i,
                  "分類": "突発停止"} for i in range(10)]
        tile = plate_graph.stop_item_tile(stops).as_dict()
        self.assertEqual(len(tile["slices"]), plate_graph.DONUT_LIMIT + 1)
        self.assertEqual(tile["slices"][-1]["label"], "その他(まとめ)")
        # 表のほうには**全部**出ます
        self.assertEqual(len(plate_graph.stop_table_tile(stops).rows), 10)

    def test_ラインが2つならライン別に重ねる(self) -> None:
        write_day(self.base, "L-2", "2026-11-02")
        got = plate_source.collect(self.base, ["L-1", "L-2"], DAY1, DAY30)
        tile = self.tiles(got)["trend_weight"]
        self.assertEqual(tile["kind"], plate_graph.KIND_COMBO)
        self.assertEqual([s["title"] for s in tile["series"]], ["L-1", "L-2"])

    def test_データが無ければ無いと言う(self) -> None:
        empty = plate_source.Collected()
        tiles = self.tiles(empty)
        self.assertFalse(tiles["stop_kinds"]["has_data"])
        self.assertIn("停止はありません", tiles["stop_kinds"]["empty"])


class WriteTests(WebCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        for day in (1, 2):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        self.got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.out = self.tmp / "出力"

    def test_CSVは1本で_Excelがそのまま開ける書き方(self) -> None:
        written = plate_out.write(self.got, self.out, kind="csv",
                                  start="2026-11-01", end="2026-11-30")
        path = Path(written["path"])
        self.assertEqual(path.name, "板集計_L-1_2026-11-01-2026-11-30.csv")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))   # BOM付きUTF-8
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0].split(",")[:3], ["日付", "ライン", "直"])
        self.assertEqual(len(lines), 1 + 6)

    def test_名前は1日なら日付1つ(self) -> None:
        self.assertEqual(plate_out.file_stem(["L-1"], "2026-11-17",
                                             "2026-11-17"),
                         "板集計_L-1_2026-11-17")
        self.assertEqual(plate_out.file_stem(["A", "B", "C", "D"], "1", "1"),
                         "板集計_4ライン_1")
        # 名前に使えない字は外します
        self.assertNotIn("/", plate_out.file_stem(["L/1"], "1", "1"))

    # -- Excel ------------------------------------------------------
    def parts(self, path: Path) -> dict[str, str]:
        with zipfile.ZipFile(path) as zf:
            return {name: zf.read(name).decode("utf-8")
                    for name in zf.namelist()}

    def test_Excelの中身は全部読める形(self) -> None:
        written = plate_out.write(self.got, self.out, kind="xlsx",
                                  start="2026-11-01", end="2026-11-30")
        parts = self.parts(Path(written["path"]))
        for name, body in parts.items():
            if name.endswith(".xml") or name.endswith(".rels"):
                ET.fromstring(body)                # 壊れていたらここで落ちます
        for needed in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                       "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                       "xl/worksheets/sheet1.xml", "xl/charts/chart1.xml",
                       "xl/drawings/drawing1.xml",
                       "xl/drawings/_rels/drawing1.xml.rels",
                       "xl/worksheets/_rels/sheet1.xml.rels"):
            self.assertIn(needed, parts)

    def test_グラフは表のセルを指す(self) -> None:
        """値を写しません ── 表を直すとグラフが追います。"""
        written = plate_out.write(self.got, self.out, kind="xlsx",
                                  start="2026-11-01", end="2026-11-30")
        parts = self.parts(Path(written["path"]))
        refs = re.findall(r"<c:f>(.*?)</c:f>", parts["xl/charts/chart1.xml"])
        self.assertTrue(refs)
        for ref in refs:
            self.assertTrue(ref.startswith("'集計'!$"), ref)
        # 見出しの行と、中身の行がずれていないこと(覚え書きのぶん下がる)。
        # 集計シートは**1行=1日**なので、行数は日数ぶんです
        rows = {int(n) for ref in refs for n in re.findall(r"\$(\d+)", ref)}
        days = len(self.got.dates)
        # いちばん小さい行は**見出し**(系列の名前が指す行)、そこから
        # 日数ぶんが中身です
        self.assertEqual(max(rows) - min(rows), days)

    def test_シートは絵から入る並び(self) -> None:
        written = plate_out.write(self.got, self.out, kind="xlsx",
                                  start="2026-11-01", end="2026-11-30")
        parts = self.parts(Path(written["path"]))
        names = re.findall(r'<sheet name="(.*?)"', parts["xl/workbook.xml"])
        self.assertEqual(names[0], xlsx_out.CHART_SHEET)
        self.assertEqual(names[1], plate_out.SHEET_AGGREGATE)
        self.assertIn(plate_out.SHEET_STOP, names)
        self.assertIn(plate_out.SHEET_STOP_TOTAL, names)

    def test_数は数のセルで入れる(self) -> None:
        """字で入れると合計も並べ替えもできず、グラフも描けません。"""
        written = plate_out.write(self.got, self.out, kind="xlsx",
                                  start="2026-11-01", end="2026-11-30")
        body = self.parts(Path(written["path"]))["xl/worksheets/sheet2.xml"]
        self.assertIn("<v>100</v>", body)              # 合計枚数
        self.assertIn("<v>12.5</v>", body)             # 合計重量
        self.assertIn("<t>2026-11-01</t>", body)       # 日付は字のまま

    def test_読めなかったものはシートの頭に書く(self) -> None:
        folder = write_day(self.base, "L-1", "2026-11-03")
        next(folder.glob("集計_*.csv")).unlink()
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        notes = plate_out.notes_of(got, "2026-11-01", "2026-11-30")
        self.assertTrue(any("読めなかったもの" in line for line in notes))
        self.assertTrue(any("2026-11-03" in line for line in notes))

    def test_列の字は外枠を壊さない(self) -> None:
        """`&` や `<` が入った内訳名でも、開ける Excel を書きます。"""
        sheet = xlsx_out.Sheet(name="試験", columns=["<欄&1>"],
                               rows=[['A<B&C"D']])
        path = xlsx_out.write(self.out / "字.xlsx", [sheet], [])
        with zipfile.ZipFile(path) as zf:
            ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    def test_列の名前は26列を超えても続く(self) -> None:
        self.assertEqual(xlsx_out.column_letter(0), "A")
        self.assertEqual(xlsx_out.column_letter(25), "Z")
        self.assertEqual(xlsx_out.column_letter(26), "AA")
        self.assertEqual(xlsx_out.column_letter(27), "AB")


class RouteTests(WebCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        for day in (1, 2):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        user_settings.save({"plate_dir": str(self.base),
                            "output_dir": str(self.tmp / "出力")})

    def ask(self, path: str, body: dict):
        return self.request("POST", path, body, screen_id=self.claim())

    def test_参照パスのラインと期間の既定を返す(self) -> None:
        status, data = self.request("GET", "/api/plate",
                                    screen_id=self.claim())
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual([one["name"] for one in data["lines"]], ["L-1"])
        self.assertEqual(data["span"]["end"], "2026-11-02")

    def test_参照パスが未設定なら画面に出す(self) -> None:
        user_settings.save({"plate_dir": ""})
        _status, data = self.request("GET", "/api/plate",
                                     screen_id=self.claim())
        self.assertFalse(data["ok"])
        self.assertIn("板データ参照パス", data["problem"])

    def test_グラフを返す(self) -> None:
        status, data = self.ask("/api/plate/graph",
                                {"lines": ["L-1"], "start": "2026-11-01",
                                 "end": "2026-11-30"})
        self.assertEqual(status, 200)
        self.assertEqual(data["summary"]["rows"], 6)
        self.assertIn("tiles", data)
        self.assertEqual(data["tiles"][0]["key"], "weight")

    def test_一覧に無いラインは断る(self) -> None:
        """参照パスの外を読ませません。"""
        for name in ("よそ", r"..\..\Windows", "../etc"):
            status, data = self.ask("/api/plate/graph",
                                    {"lines": [name], "start": "2026-11-01",
                                     "end": "2026-11-30"})
            self.assertEqual(status, 400)
            self.assertIn("そのラインはありません", data["reason"])

    def test_ラインを選んでいなければ断る(self) -> None:
        status, data = self.ask("/api/plate/graph",
                                {"lines": [], "start": "2026-11-01",
                                 "end": "2026-11-30"})
        self.assertEqual(status, 400)
        self.assertIn("ラインが選ばれていません", data["reason"])

    def test_期間が読めなければ断る(self) -> None:
        status, data = self.ask("/api/plate/graph",
                                {"lines": ["L-1"], "start": "11月",
                                 "end": "2026-11-30"})
        self.assertEqual(status, 400)
        self.assertIn("期間が読めません", data["reason"])

    def test_CSVで書き出す(self) -> None:
        status, data = self.ask("/api/plate/export",
                                {"lines": ["L-1"], "start": "2026-11-01",
                                 "end": "2026-11-30", "kind": "csv"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        path = Path(data["written"]["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".csv")
        self.assertEqual(path.parent, self.tmp / "出力")

    def test_Excelで書き出す(self) -> None:
        _status, data = self.ask("/api/plate/export",
                                 {"lines": ["L-1"], "start": "2026-11-01",
                                  "end": "2026-11-30", "kind": "xlsx"})
        path = Path(data["written"]["path"])
        self.assertEqual(path.suffix, ".xlsx")
        with zipfile.ZipFile(path) as zf:
            self.assertIn("xl/charts/chart1.xml", zf.namelist())

    def test_知らない形では出さない(self) -> None:
        status, _data = self.ask("/api/plate/export",
                                 {"lines": ["L-1"], "start": "2026-11-01",
                                  "end": "2026-11-30", "kind": "pdf"})
        self.assertEqual(status, 400)

    def test_出力先が無ければ断る(self) -> None:
        user_settings.save({"output_dir": ""})
        status, data = self.ask("/api/plate/export",
                                {"lines": ["L-1"], "start": "2026-11-01",
                                 "end": "2026-11-30", "kind": "csv"})
        self.assertEqual(status, 400)
        self.assertIn("出力先", data["reason"])

    def test_読めるものが無ければ書かない(self) -> None:
        _status, data = self.ask("/api/plate/export",
                                 {"lines": ["L-1"], "start": "2026-01-01",
                                  "end": "2026-01-31", "kind": "csv"})
        self.assertFalse(data["ok"])
        self.assertIn("読める集計がありませんでした", data["message"])
        self.assertFalse(list((self.tmp / "出力").glob("*.csv"))
                         if (self.tmp / "出力").is_dir() else [])


class ExcelShapeTests(WebCase):
    r"""Excel の作りは、**VBAが出していた月別集計のブックに合わせます**

    現場から出た指摘は3つでした。

        稼働率と生産性のスケールが合っていない(生産性が折れ線に見えない)
        直別の重量・枚数が直別になっていない
        停止の分類は円グラフにしてほしい

    どれも**集計シートの形**から来ていました。向こうのブックは
    **1行=1日**で、直は行ではなく**列**で分かれています ── 直を行に
    持ったままでは、グラフの系列を直ごとに分けられません。
    """

    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        for day in (1, 2, 3):
            write_day(self.base, "L-1", f"2026-11-{day:02d}")
        self.got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        self.out = self.tmp / "出力"

    def book(self, collected=None) -> dict[str, str]:
        written = plate_out.write(collected or self.got, self.out, kind="xlsx",
                                  start="2026-11-01", end="2026-11-30")
        with zipfile.ZipFile(written["path"]) as zf:
            return {name: zf.read(name).decode("utf-8")
                    for name in zf.namelist()}

    def charts(self, parts: dict[str, str]) -> dict[str, str]:
        """題 → そのグラフの XML。"""
        found = {}
        for name, body in parts.items():
            if not name.startswith("xl/charts/chart"):
                continue
            title = re.findall(r"<a:t>(.*?)</a:t>", body)[0]
            found[title] = body
        return found

    # -- シートの形 --------------------------------------------------
    def test_集計シートは1行が1日で直は列(self) -> None:
        sheet = plate_out._daily_sheet(self.got, [])
        self.assertEqual(len(sheet.rows), 3)          # 3日(直ごとの9行ではない)
        for shift in ("1直", "2直", "3直"):
            self.assertIn(f"{shift}合計(枚)", sheet.columns)
            self.assertIn(f"{shift}合計(t)", sheet.columns)
            self.assertIn(f"生産性{shift}(t/h)", sheet.columns)
        self.assertEqual(sheet.columns[0], "日付")
        self.assertNotIn("ライン", sheet.columns)     # 1ラインなら列を足さない

    def test_ラインが複数ならライン列を足す(self) -> None:
        write_day(self.base, "L-2", "2026-11-01")
        got = plate_source.collect(self.base, ["L-1", "L-2"], DAY1, DAY30)
        sheet = plate_out._daily_sheet(got, [])
        self.assertEqual(sheet.columns[1], "ライン")
        self.assertEqual(len(sheet.rows), 4)          # 3日 + L-2 の1日

    def test_累計はラインごとに積む(self) -> None:
        write_day(self.base, "L-2", "2026-11-01")
        write_day(self.base, "L-2", "2026-11-02")
        got = plate_source.collect(self.base, ["L-1", "L-2"], DAY1, DAY30)
        sheet = plate_out._daily_sheet(got, [])
        at = sheet.columns.index("日毎累計(枚)")
        line_at = sheet.columns.index("ライン")
        for name in ("L-1", "L-2"):
            same = [r for r in sheet.rows if r[line_at] == name]
            self.assertEqual(same[0][at] * 2, same[1][at])

    def test_直の合計と全体の合計が合う(self) -> None:
        sheet = plate_out._daily_sheet(self.got, [])
        total = sheet.columns.index("合計(枚)")
        parts = [sheet.columns.index(f"{s}合計(枚)")
                 for s in ("1直", "2直", "3直")]
        for row in sheet.rows:
            self.assertEqual(row[total], sum(row[i] for i in parts))

    # -- グラフ ------------------------------------------------------
    def test_稼働率と生産性は別のグラフ(self) -> None:
        """90 と 3 を同じ目盛りに乗せると、生産性が床に貼り付きます。"""
        charts = self.charts(self.book())
        self.assertIn("稼働率(％)", charts)
        self.assertIn("生産性(t/h)", charts)
        self.assertNotIn("稼働率と生産性", charts)
        # 稼働率は折れ線1本、生産性は直ごとの棒
        self.assertIn("lineChart", charts["稼働率(％)"])
        self.assertEqual(charts["稼働率(％)"].count("<c:ser>"), 1)
        self.assertIn("barChart", charts["生産性(t/h)"])
        self.assertEqual(charts["生産性(t/h)"].count("<c:ser>"), 3)

    def test_直別のグラフは直ごとに系列を分ける(self) -> None:
        charts = self.charts(self.book())
        for title in ("直重量(t)", "直枚数(枚)"):
            body = charts[title]
            self.assertEqual(body.count("<c:ser>"), 3, title)
            names = re.findall(r"<c:tx><c:strRef><c:f>(.*?)</c:f>", body)
            self.assertEqual(len(set(names)), 3, title)   # 別々の列を見ている

    def test_停止の分類は円グラフ(self) -> None:
        charts = self.charts(self.book())
        pies = {t: b for t, b in charts.items() if "pieChart" in b}
        self.assertTrue(pies)
        for title, body in pies.items():
            self.assertTrue(title.startswith("「"), title)
            self.assertIn("'停止集計'!", body)
            self.assertIn('<c:varyColors val="1"/>', body)
            self.assertIn("<c:dPt>", body)          # 扇ごとに色を指す
            self.assertNotIn("<c:catAx>", body)     # 円に軸はありません

    def test_円グラフは分類ごとの行だけを見る(self) -> None:
        sheet, spans = plate_out._stop_pie_sheet(self.got.stops)
        self.assertEqual([kind for kind, _, _ in spans], ["管理ロス停止"])
        charts = self.charts(self.book())
        body = charts["「管理ロス停止」(分)"]
        refs = re.findall(r"<c:f>'停止集計'!\$([A-Z])\$(\d+):\$[A-Z]\$(\d+)",
                          body)
        # 内訳の列(B)と分の列(D)を、同じ行の範囲で見ている
        kinds = {col: (int(a), int(b)) for col, a, b in refs}
        self.assertEqual(kinds["B"], kinds["D"])
        self.assertEqual(kinds["B"][1] - kinds["B"][0] + 1, 2)   # 休憩食事・ﾌｫｰｸ待ち

    def test_累積枚数は右の目盛りにする(self) -> None:
        """直枚数(数百)と累積(数万)を同じ目盛りに乗せると読めません。"""
        body = self.charts(self.book())["直枚数・累積枚数推移"]
        self.assertIn("barChart", body)
        self.assertIn("lineChart", body)
        self.assertIn(xlsx_out.AX_VAL2, body)
        self.assertIn('<c:crosses val="max"/>', body)
        # 第2軸の横軸は**消します**(日付が2段に出てしまうため)
        self.assertIn('<c:delete val="1"/>', body)

    def test_系列の色は自前で指す(self) -> None:
        """テーマを入れていないので、色を指さないと**白く塗られます**。"""
        charts = self.charts(self.book())
        for title, body in charts.items():
            self.assertIn("<a:srgbClr val=", body, title)

    def test_シートの並びは絵から(self) -> None:
        parts = self.book()
        names = re.findall(r'<sheet name="(.*?)"', parts["xl/workbook.xml"])
        self.assertEqual(names, [xlsx_out.CHART_SHEET, plate_out.SHEET_DAILY,
                                 plate_out.SHEET_STOP_TOTAL,
                                 plate_out.SHEET_STOP, plate_out.SHEET_SOURCE])

    def test_元のままの行も残す(self) -> None:
        """数が合わないときに、どこで変わったのかを追えるように。"""
        parts = self.book()
        body = parts["xl/worksheets/sheet5.xml"]       # 元データ
        self.assertIn("<t>1直</t>", body)
        self.assertIn("<t>2026-11-01</t>", body)

    def test_停止が無くても書ける(self) -> None:
        for folder in self.base.rglob("停止内訳_*.csv"):
            folder.unlink()
        got = plate_source.collect(self.base, ["L-1"], DAY1, DAY30)
        charts = self.charts(self.book(got))
        self.assertFalse([t for t, b in charts.items() if "pieChart" in b])
        self.assertIn("直重量(t)", charts)


class LotSearchTests(WebCase):
    r"""ロットを探す ── 期間とロット№で、集計明細CSVから当たった行を出す

    日報管理ツールの「ロットを探す」の移植です。肝は3つ。

        前方一致で、**全角で打っても当たる**(現場の IME はよく全角です)
        空欄は断る(期間の全部を出しても、上限に当たるだけ)
        **読むだけ**(探しても何も書きません)
    """

    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        write_day(self.base, "L-1", "2026-11-01",
                  lots=[("1直", 1, 1, "N7131T0"), ("1直", 1, 2, "N7132T0"),
                        ("2直", 2, 1, "K9001A0")])
        write_day(self.base, "L-1", "2026-11-02",
                  lots=[("3直", 1, 1, "N7140T0")])
        write_day(self.base, "L-2", "2026-11-02",
                  lots=[("1直", 1, 1, "N7199Z9")])
        user_settings.save({"plate_dir": str(self.base),
                            "output_dir": str(self.tmp / "出力")})

    def find(self, lot: str, *, line="", start=DAY1, end=DAY30):
        from converter import plate_lot
        return plate_lot.search(self.base, [line] if line else [], start, end, lot)

    # -- 当たり方 ----------------------------------------------------
    def test_頭の数桁で当たる(self) -> None:
        got = self.find("N713")
        self.assertEqual(got.total, 2)                 # N7131T0 / N7132T0
        lots = [row[got.columns.index("ロット№")] for row in got.rows]
        self.assertEqual(lots, ["N7131T0", "N7132T0"])
        # 桁を減らすと広がります(N7140T0 と、L-2 の N7199Z9 も)
        self.assertEqual(self.find("N71").total, 4)

    def test_全角でも小文字でも空白が入っても当たる(self) -> None:
        for text in ("Ｎ７１３１Ｔ０", "n7131t0", " N7131T0 ", "n-7131-t0"):
            self.assertEqual(self.find(text).total, 1, text)

    def test_丸ごと打っても当たる(self) -> None:
        self.assertEqual(self.find("K9001A0").total, 1)

    def test_ラインを選べる(self) -> None:
        self.assertEqual(self.find("N71").total, 4)            # 全ライン
        self.assertEqual(self.find("N71", line="L-1").total, 3)
        self.assertEqual(self.find("N7199").total, 1)          # L-2 の行
        self.assertEqual(self.find("N7199", line="L-1").total, 0)
        self.assertEqual(self.find("N7199", line="L-2").total, 1)

    def test_期間で絞る(self) -> None:
        self.assertEqual(self.find("N7140").total, 1)
        self.assertEqual(self.find("N7140", start=DAY1,
                                   end=datetime.date(2026, 11, 1)).total, 0)

    def test_作業日_ライン_直_ページ_行の順に並ぶ(self) -> None:
        got = self.find("N")
        keys = [(row[0], row[1], row[2], row[3], row[4]) for row in got.rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0][0], "2026-11-01")

    # -- 断るもの ----------------------------------------------------
    def test_空欄は断る(self) -> None:
        from converter import plate_lot
        with self.assertRaises(plate_lot.BadRequest) as caught:
            self.find("")
        self.assertIn("ロット№を入れてください", str(caught.exception))
        self.assertEqual(caught.exception.field_name, "lot")

    def test_当たらなければ理由を出す(self) -> None:
        got = self.find("ZZZZ")
        self.assertEqual(got.total, 0)
        self.assertTrue(got.warnings)
        self.assertIn("期間を広げる", got.warnings[0])

    def test_多すぎたら先の何行かだけ出す(self) -> None:
        from converter import plate_lot
        original = plate_lot.LIMIT
        plate_lot.LIMIT = 2
        self.addCleanup(setattr, plate_lot, "LIMIT", original)
        got = self.find("N")
        self.assertEqual(got.total, 4)
        self.assertEqual(got.shown, 2)
        self.assertIn("多すぎる", got.warnings[0])

    def test_明細が無ければそう言う(self) -> None:
        for path in self.base.rglob("集計明細_*.csv"):
            path.unlink()
        got = self.find("N71")
        self.assertEqual(got.total, 0)
        self.assertTrue(any("集計明細CSV" in one for one in got.warnings))

    def test_見出しが違う明細は理由を残す(self) -> None:
        path = next(self.base.rglob("集計明細_*.csv"))
        path.write_text("よその,見出し\n1,2\n", encoding="utf-8-sig")
        got = self.find("N71")
        self.assertTrue(any("ロット№" in one["reason"] for one in got.skipped))

    # -- 出す --------------------------------------------------------
    def test_表の列は紙の欄(self) -> None:
        got = self.find("N7131")
        for name in ("作業日", "ライン", "直", "ページ", "行", "ロット№",
                     "材・調質", "厚×幅×丈", "検入枚数", "開始", "終了",
                     "実績枚数", "実績重量", "作業者", "用途名", "納入先"):
            self.assertIn(name, got.columns)
        row = got.rows[0]
        self.assertEqual(row[got.columns.index("開始")], "08:15")
        self.assertEqual(row[got.columns.index("終了")], "09:40")
        self.assertEqual(row[got.columns.index("作業者")], "山田")

    def test_CSVは画面と同じ行で条件も書く(self) -> None:
        from converter import plate_lot
        got = self.find("N71")
        written = plate_lot.write_csv(got, self.tmp / "出力",
                                      start=DAY1, end=DAY30)
        path = Path(written["path"])
        self.assertTrue(path.name.startswith("ロット検索_N71_"))
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))      # BOM付きUTF-8
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        self.assertIn("ロットを探す", lines[0])
        self.assertIn("2026-11-01", lines[0])
        self.assertEqual(lines[2].split(",")[0], "作業日")
        self.assertEqual(len(lines), 3 + got.total)

    def test_探しても何も書かない(self) -> None:
        before = sorted(p.name for p in self.base.rglob("*"))
        self.find("N71")
        self.assertEqual(sorted(p.name for p in self.base.rglob("*")), before)


class LotRouteTests(WebCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        write_day(self.base, "L-1", "2026-11-01",
                  lots=[("1直", 1, 1, "N7131T0"), ("2直", 2, 1, "K9001A0")])
        user_settings.save({"plate_dir": str(self.base),
                            "output_dir": str(self.tmp / "出力")})

    def ask(self, body: dict, path="/api/plate/lots"):
        full = {"start": "2026-11-01", "end": "2026-11-30", "line": "*"}
        full.update(body)
        return self.request("POST", path, full, screen_id=self.claim())

    def test_探せる(self) -> None:
        status, data = self.ask({"lot": "n71"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["lot"], "N71")
        self.assertIn("作業日", data["columns"])

    def test_ロット番号が空なら断る(self) -> None:
        status, data = self.ask({"lot": "  "})
        self.assertEqual(status, 400)
        self.assertIn("ロット№を入れてください", data["reason"])

    def test_日付が読めなければ断る(self) -> None:
        status, data = self.ask({"lot": "N71", "start": "11月"})
        self.assertEqual(status, 400)
        self.assertIn("開始日が読めません", data["reason"])
        status, data = self.ask({"lot": "N71", "end": ""})
        self.assertEqual(status, 400)
        self.assertIn("終了日が読めません", data["reason"])

    def test_一覧に無いラインは断る(self) -> None:
        for name in ("よそ", r"..\..\Windows"):
            status, data = self.ask({"lot": "N71", "line": name})
            self.assertEqual(status, 400)
            self.assertIn("そのラインはありません", data["reason"])

    def test_全ラインは星印で受ける(self) -> None:
        _status, data = self.ask({"lot": "N", "line": "*"})
        self.assertEqual(data["total"], 1)         # N7131T0(K9001A0 は別)
        _status, data = self.ask({"lot": "K", "line": "*"})
        self.assertEqual(data["total"], 1)

    def test_CSVに出す(self) -> None:
        status, data = self.ask({"lot": "N71"}, "/api/plate/lots/export")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        path = Path(data["written"]["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.tmp / "出力")

    def test_当たらなければ書かない(self) -> None:
        _status, data = self.ask({"lot": "ZZZ"}, "/api/plate/lots/export")
        self.assertFalse(data["ok"])
        self.assertIn("当たった行がない", data["message"])
        self.assertFalse(list((self.tmp / "出力").glob("*.csv"))
                         if (self.tmp / "出力").is_dir() else [])

    def test_出力先が無ければ断る(self) -> None:
        user_settings.save({"output_dir": ""})
        status, data = self.ask({"lot": "N71"}, "/api/plate/lots/export")
        self.assertEqual(status, 400)
        self.assertIn("出力先", data["reason"])

    def test_参照パスが無ければ断る(self) -> None:
        user_settings.save({"plate_dir": ""})
        status, data = self.ask({"lot": "N71"})
        self.assertEqual(status, 400)
        self.assertIn("板データ参照パス", data["detail"])


class PaperTests(WebCase):
    r"""集計に入っていないページを、紙(印刷用HTML)から拾う

    日報管理ツールの「集計から除く」で外した作業者のページは、**集計に
    入らないので集計明細CSVにも出ません**(外れるのは集計を作るところ
    ── `services/agg_exclude`)。ページそのものは消えず、紙は残るので、
    そこから拾います。向こうの「ロットを探す」でも除く人のページは出る
    ので、**同じ範囲で探せる**ようにするためです。
    """

    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()

    def put_paper(self, line: str, stamp: str, shift: str, page: int,
                  text: str = "") -> Path:
        """紙を1枚置く(`<ライン>\\印刷\\年.月\\日\\印刷_….html`)。"""
        parts = stamp.split("-")
        folder = (self.base / line / "印刷" / f"{parts[0]}.{parts[1]}"
                  / parts[2])
        folder.mkdir(parents=True, exist_ok=True)
        day = f"{int(parts[0])}年{int(parts[1])}月{int(parts[2])}日"
        path = folder / f"印刷_{day}_{line}_{shift}_{page}.html"
        path.write_text(text or SAMPLE, encoding="utf-8")
        return path

    # -- 読み手 ------------------------------------------------------
    def test_本物の紙を読める(self) -> None:
        """見出しは**集計明細CSVと同じ字**になります(位置ではなく名前で拾うため)。"""
        sheet = plate_paper.parse(SAMPLE)[0]
        self.assertEqual(sheet.columns[0], "ロット№")
        self.assertIn("梱包作業時間 開始 時", sheet.columns)
        self.assertIn("製品寸法 厚×幅×丈", sheet.columns)
        self.assertIn("実績合計 重量", sheet.columns)
        self.assertEqual(sheet.worker, "除外 太郎")
        self.assertTrue(sheet.excluded)             # 「自動」の印
        # 打ってある行だけ(紙は12行ぶん罫線がありますが、空行は拾いません)
        self.assertEqual([row["ロット№"] for row in sheet.rows],
                         ["X7101", "X7103"])
        self.assertEqual([row["行"] for row in sheet.rows], ["1", "3"])
        one = sheet.rows[0]
        self.assertEqual(one["製品寸法 厚×幅×丈"], "3.2×1219×2438")
        self.assertEqual(one["梱包作業時間 開始 時"], "08")
        self.assertEqual(one["実績合計 重量"], "2.40")

    def test_合計の表は読まない(self) -> None:
        """紙には明細のあとに合計が続きます。ロットの行は明細だけです。"""
        sheet = plate_paper.parse(SAMPLE)[0]
        self.assertEqual(len(sheet.rows), 2)

    def test_壊れていても落ちない(self) -> None:
        self.assertEqual(plate_paper.parse("<html><p>なにか"), [])
        path = self.put_paper("L-1", "2026-11-17", "1直", 1, text="壊れています")
        self.assertIsNone(plate_paper.read(path))

    def test_ファイル名から日付とラインと直とページを取る(self) -> None:
        self.put_paper("L-1", "2026-11-17", "2直", 3)
        found = plate_paper.pages(self.base, "L-1", DAY1, DAY30)
        self.assertEqual([(d, line, shift, page)
                          for d, line, shift, page, _ in found],
                         [("2026-11-17", "L-1", "2直", "3")])
        # 期間の外は並べません
        self.assertEqual(plate_paper.pages(self.base, "L-1",
                                           datetime.date(2026, 10, 1),
                                           datetime.date(2026, 10, 31)), [])

    # -- 探すほうと合わせて ------------------------------------------
    def find(self, lot: str):
        from converter import plate_lot
        return plate_lot.search(self.base, ["L-1"], DAY1, DAY30, lot)

    def test_集計に無いページのロットも出る(self) -> None:
        """**ここが本題。** 集計から除かれたロットも探せます。"""
        # 1ページ目は集計に入り、2ページ目は集計から除かれた(紙だけ残る)
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "N7131T0")])
        self.put_paper("L-1", "2026-11-17", "1直", 2)
        from converter import plate_lot
        got = self.find("X71")
        self.assertEqual(got.total, 2)                  # X7101 / X7103
        at = got.columns.index("どこから")
        self.assertEqual({row[at] for row in got.rows},
                         {plate_lot.FROM_PAPER_EXCLUDED})
        # 紙に無い欄(用途名・納入先)は空のまま ── 無いと分かるように
        self.assertEqual(got.rows[0][got.columns.index("用途名")], "")
        self.assertEqual(got.rows[0][got.columns.index("作業者")], "除外 太郎")
        # 紙の行には日付・ライン・直が入っていないので、入れ直します
        self.assertEqual(got.rows[0][got.columns.index("作業日")], "2026-11-17")
        self.assertEqual(got.rows[0][got.columns.index("ライン")], "L-1")
        self.assertEqual(got.rows[0][got.columns.index("直")], "1直")
        self.assertEqual(got.rows[0][got.columns.index("ページ")], "2")

    def test_集計にあるページの紙は開かない(self) -> None:
        """同じページを二重に数えません(**ふだんは1枚も開きません**)。"""
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "X7101"), ("1直", 1, 3, "X7103")])
        self.put_paper("L-1", "2026-11-17", "1直", 1)
        from converter import plate_lot
        got = self.find("X71")
        self.assertEqual(got.total, 2)
        at = got.columns.index("どこから")
        self.assertEqual({row[at] for row in got.rows}, {plate_lot.FROM_DETAIL})

    def test_集計明細が1本も無くても紙から探せる(self) -> None:
        self.put_paper("L-1", "2026-11-17", "1直", 1)
        got = self.find("X7103")
        self.assertEqual(got.total, 1)
        self.assertEqual(got.rows[0][got.columns.index("ロット№")], "X7103")

    def test_開く紙が多すぎたらそこでやめる(self) -> None:
        original = plate_paper.MAX_FILES
        plate_paper.MAX_FILES = 1
        self.addCleanup(setattr, plate_paper, "MAX_FILES", original)
        for page in (1, 2, 3):
            self.put_paper("L-1", "2026-11-17", "1直", page)
        got = self.find("X71")
        self.assertTrue(any("紙が" in one for one in got.warnings))


def make_report_db(path: Path, line: str, rows, *, page_column: str = "頁",
                   worker: str = "山田") -> Path:
    r"""共有の日報データ(`日報データ.sqlite3`)を作る。

    表の名前も列も、日報管理ツールが作るものと同じです
    (`access_bridge/sqlite_backend.py` の `HEADER_COLUMNS` / `DETAIL_COLUMNS`)。
    `rows` は `(報告日, 直, 頁, 行番号, LOT)`。
    """
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    head = f'T_日報ヘッダー_{line}'
    detail = f'T_日報明細_{line}'
    conn.execute(f'CREATE TABLE "{head}" ("報告日" TEXT, "ライン" TEXT, "直" TEXT,'
                 f' "{page_column}" INTEGER, "担当者" TEXT, "昼勤" TEXT,'
                 ' "枚数" TEXT, "重量Kg" TEXT, "理由" TEXT, "保存日時" TEXT)')
    conn.execute(f'CREATE TABLE "{detail}" ("報告日" TEXT, "ライン" TEXT, "直" TEXT,'
                 f' "{page_column}" INTEGER, "行番号" INTEGER, "LOT" TEXT,'
                 ' "ZAI" TEXT, "SIZ" TEXT, "KEN" TEXT, "KZ" TEXT, "KH" TEXT,'
                 ' "SZ" TEXT, "SH" TEXT, "CON" TEXT, "WEI" TEXT, "TIM" TEXT,'
                 ' "Others2" TEXT, "Others3" TEXT)')
    seen = set()
    for stamp, shift, page, row_no, lot in rows:
        if (stamp, shift, page) not in seen:
            seen.add((stamp, shift, page))
            conn.execute(f'INSERT INTO "{head}" VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (stamp, line, shift, page, worker, "有", "100",
                          "2400", "", "2026-11-17 18:00"))
        conn.execute(f'INSERT INTO "{detail}" VALUES'
                     ' (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (stamp, line, shift, page, row_no, lot, "SS400",
                      "3.2×1219×2438", "40", "08", "15", "09", "40",
                      "40", "2.40", "85", "外販", "〇〇鋼材"))
    conn.commit()
    conn.close()
    return path


class ReportDbTests(WebCase):
    r"""共有の日報データ ── **ロットを探すの本命**

    日報管理ツールの各端末が「共有へ保存」で送っている先そのものです
    (控え(LocalBackup)ではありません)。ここを読めば、向こうの
    「ロットを探す」と**同じ範囲**になります ── 集計から除いた作業者の
    ページも、紙を出していないページも入っています。
    """

    def setUp(self) -> None:
        super().setUp()
        self.base = self.tmp / "板"
        self.base.mkdir()
        self.db = make_report_db(
            self.tmp / "共有" / "日報データ.sqlite3", "L-1",
            [("2026年11月17日", "1直", 1, 1, "N7131T0"),
             ("2026年11月17日", "1直", 1, 2, "N7132T0"),
             ("2026年11月18日", "2直", 2, 1, "X9001A0")])

    def find(self, lot: str, *, shared=None, lines=None):
        from converter import plate_lot
        # `shared=False` で「指していない」、省略で既定の共有を使います
        use = self.db if shared is None else (shared or None)
        return plate_lot.search(self.base, lines or ["L-1"], DAY1, DAY30, lot,
                                shared=use)

    # -- 読み手 ------------------------------------------------------
    def test_日付の書き方は年月日でも読む(self) -> None:
        from converter import report_db
        self.assertEqual(report_db.parse_report_date("2026年11月17日"),
                         datetime.date(2026, 11, 17))
        self.assertEqual(report_db.parse_report_date("2026-11-17"),
                         datetime.date(2026, 11, 17))
        self.assertIsNone(report_db.parse_report_date("なにか"))

    def test_表の名前からラインを拾う(self) -> None:
        import sqlite3

        from converter import report_db
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(sorted(report_db.tables(conn)), ["L-1"])

    def test_ページの列は頁でもページでも読む(self) -> None:
        path = make_report_db(self.tmp / "共有2" / "日報データ.sqlite3", "L-2",
                              [("2026年11月17日", "1直", 3, 1, "K9001A0")],
                              page_column="ページ")
        got = self.find("K900", shared=path, lines=["L-2"])
        self.assertEqual(got.total, 1)
        self.assertEqual(got.rows[0][got.columns.index("ページ")], "3")

    def test_読むだけで書かない(self) -> None:
        """`?mode=ro` で開きます ── 無いファイルを作らず、書けません。"""
        before = self.db.stat().st_mtime_ns
        self.find("N71")
        self.assertEqual(self.db.stat().st_mtime_ns, before)
        self.assertFalse((self.tmp / "共有" / "日報データ.sqlite3-wal").exists())

    # -- 探すほうと合わせて ------------------------------------------
    def test_共有から探せる(self) -> None:
        got = self.find("N713")
        self.assertEqual(got.total, 2)
        at = got.columns.index("どこから")
        from converter import plate_lot
        self.assertEqual({row[at] for row in got.rows}, {plate_lot.FROM_SHARED})
        row = got.rows[0]
        self.assertEqual(row[got.columns.index("作業日")], "2026-11-17")
        self.assertEqual(row[got.columns.index("開始")], "08:15")
        self.assertEqual(row[got.columns.index("用途名")], "外販")
        self.assertEqual(row[got.columns.index("作業者")], "山田")
        self.assertIn("共有の日報データ", got.scope)

    def test_集計に無いページも出る(self) -> None:
        """**ここが本題。** 集計から除いたページも共有には残っています。"""
        write_day(self.base, "L-1", "2026-11-18",
                  lots=[("1直", 1, 1, "N7131T0")])     # 集計に入ったページだけ
        got = self.find("X9001")                        # 集計に無いロット
        self.assertEqual(got.total, 1)
        self.assertEqual(got.rows[0][got.columns.index("ページ")], "2")

    def test_同じページを二重に出さない(self) -> None:
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "N7131T0"), ("1直", 1, 2, "N7132T0")])
        got = self.find("N713")
        self.assertEqual(got.total, 2)                  # 共有のぶんだけ
        from converter import plate_lot
        at = got.columns.index("どこから")
        self.assertEqual({row[at] for row in got.rows}, {plate_lot.FROM_SHARED})

    def test_共有を指していなければ集計明細で探す(self) -> None:
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "N7131T0")])
        got = self.find("N713", shared=False)
        from converter import plate_lot
        self.assertEqual(got.total, 1)
        self.assertEqual(got.rows[0][got.columns.index("どこから")],
                         plate_lot.FROM_DETAIL)
        self.assertIn("集計明細", got.scope)

    # -- 読めないとき ------------------------------------------------
    def test_無ければ理由を出して探し続ける(self) -> None:
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "N7131T0")])
        got = self.find("N713", shared=self.tmp / "共有" / "まだ無い.sqlite3")
        self.assertEqual(got.total, 1)                  # 集計明細から出ます
        self.assertTrue(any("まだ無い" in one for one in got.warnings))

    def test_Accessは読まない(self) -> None:
        """ロット№の比べ方が Access の SQL では書けません(向こうも読みません)。"""
        path = self.tmp / "共有" / "日報データ.accdb"
        path.write_bytes(b"dummy")
        got = self.find("N713", shared=path)
        self.assertTrue(any("Access" in one for one in got.warnings))

    def test_壊れていても探し続ける(self) -> None:
        path = self.tmp / "共有" / "壊れ.sqlite3"
        path.write_text("これはDBではありません", encoding="utf-8")
        write_day(self.base, "L-1", "2026-11-17",
                  lots=[("1直", 1, 1, "N7131T0")])
        got = self.find("N713", shared=path)
        self.assertEqual(got.total, 1)
        self.assertTrue(got.warnings)

    def test_多すぎたらそこでやめる(self) -> None:
        from converter import report_db
        original = report_db.CAP
        report_db.CAP = 1
        self.addCleanup(setattr, report_db, "CAP", original)
        got = self.find("N713")
        self.assertTrue(any("超えた" in one for one in got.warnings))
