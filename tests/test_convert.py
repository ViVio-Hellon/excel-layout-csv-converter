"""変換器 ── 読めること・数が合うこと・同じCSVが出ること

【実物は置いていません】
作業者名も納入先も実名なので、リポジトリには入れられません。かわりに
**同じ座標へ同じ形の値を置いたブック**を組み立てて試します
(`tests/_fixture.py`)。

実物との突き合わせは別にやっています ── 実機の13本(コイル7 / 板6)で、
シート自身の合計と**9項目すべて一致**、日報管理ツール本体の出力と
**48本すべてバイト一致**でした(README「確かめたこと」)。
"""
from __future__ import annotations

import csv
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

# **参照用マスタはテストに要りません。** 読めない warning がファイルの数だけ
# 出て、本当に見たい失敗が埋もれるので黙らせます(本番は `convert.py` が
# 1行だけ出します)
logging.getLogger("nippou").setLevel(logging.CRITICAL)

from converter import check, run, totals         # noqa: E402
from tests._fixture import day_sheet, write_xlsx  # noqa: E402


class GatherTests(unittest.TestCase):
    """読む相手の集め方。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_フォルダごと拾う(self) -> None:
        for n in range(3):
            write_xlsx(self.tmp / f"{n}.xlsx", day_sheet())
        self.assertEqual(len(run.gather([self.tmp])), 3)

    def test_名前順に並ぶ(self) -> None:
        """**入れる順で結果が変わらないように。**"""
        for name in ("c.xlsx", "a.xlsx", "b.xlsx"):
            write_xlsx(self.tmp / name, day_sheet())
        found = [p.name for p in run.gather([self.tmp])]
        self.assertEqual(found, ["a.xlsx", "b.xlsx", "c.xlsx"])

    def test_xlsx以外は拾わない(self) -> None:
        write_xlsx(self.tmp / "a.xlsx", day_sheet())
        (self.tmp / "メモ.txt").write_text("x", encoding="utf-8")
        (self.tmp / "古い.xls").write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in run.gather([self.tmp])], ["a.xlsx"])

    def test_エクセルの一時ファイルは拾わない(self) -> None:
        """**`~$` は開きっぱなしの目印**で、中身はブックではありません。"""
        write_xlsx(self.tmp / "a.xlsx", day_sheet())
        (self.tmp / "~$a.xlsx").write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in run.gather([self.tmp])], ["a.xlsx"])

    def test_ファイルを直に渡せる(self) -> None:
        one = write_xlsx(self.tmp / "a.xlsx", day_sheet())
        self.assertEqual(run.gather([one]), [one])


class ConvertTests(unittest.TestCase):
    """変換そのもの。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.out = self.tmp / "out"

    def book(self, name="a.xlsx", **kw) -> Path:
        return write_xlsx(self.tmp / name, day_sheet(**kw))

    def test_読めてCSVが出る(self) -> None:
        self.book()
        found = run.convert([self.tmp], self.out, line="HVC")
        self.assertEqual(len(found.read_ok), 1)
        # 集計 / 集計明細 / 停止内訳 / 計算内容 の4本
        self.assertEqual(len(found.written), 4)
        self.assertTrue(all(p.exists() for p in found.written))

    def test_3直ぶん読む(self) -> None:
        self.book()
        found = run.convert([self.tmp], self.out, line="HVC")
        one = found.files[0]
        self.assertEqual(one.report_date, "2026年8月26日")
        self.assertEqual(one.pages, 3)       # 1直2行 + 2直1行 + 3直1行
        self.assertEqual(one.rows, 4)

    def test_シートの呼び名からラインを当てる(self) -> None:
        self.book()
        found = run.convert([self.tmp], self.out)   # --line を渡さない
        self.assertEqual(found.files[0].line, "HVC")

    def test_ラインが決まらなければ断る(self) -> None:
        """**黙って当てずっぽうで入れません。**"""
        write_xlsx(self.tmp / "a.xlsx", day_sheet(line_text="知らない名前"))
        found = run.convert([self.tmp], self.out)
        self.assertFalse(found.files[0].ok)
        self.assertIn("ラインが決まりません", found.files[0].error)

    def test_指定したラインが優先される(self) -> None:
        self.book()
        found = run.convert([self.tmp], self.out, line="LS")
        self.assertEqual(found.files[0].line, "LS")

    def test_1本読めなくても残りは続く(self) -> None:
        """**1本のせいで全部が止まると、何百本のときに困ります。**"""
        self.book("a.xlsx")
        (self.tmp / "b.xlsx").write_bytes("xlsx ではありません".encode("utf-8"))
        self.book("c.xlsx")
        found = run.convert([self.tmp], self.out, line="HVC")
        self.assertEqual(len(found.files), 3)
        self.assertEqual(len(found.read_ok), 2)

    def test_確かめるだけなら1本も書かない(self) -> None:
        self.book()
        found = run.convert([self.tmp], self.out, line="HVC", dry_run=True)
        self.assertEqual(len(found.read_ok), 1)
        self.assertEqual(found.written, [])
        self.assertFalse(self.out.exists())

    def test_DBを残さない(self) -> None:
        """**使い捨て。** 変換のあとにDBが残っていたら、次の変換に混ざります。"""
        self.book()
        run.convert([self.tmp], self.out, line="HVC")
        self.assertEqual(list(self.out.rglob("*.sqlite3")), [])

    def test_コイルでも板でも読める(self) -> None:
        """**座標は同じ。** 使う欄が違うだけです(`docs/座標表.md`)。"""
        for coil in (True, False):
            with self.subTest(coil=coil):
                tmp = Path(tempfile.mkdtemp(dir=self.tmp))
                write_xlsx(tmp / "a.xlsx", day_sheet(coil=coil))
                found = run.convert([tmp], self.out / str(coil), line="HVC")
                self.assertTrue(found.files[0].ok)
                self.assertTrue(found.files[0].verify.ok)


class TallyMaterialTests(unittest.TestCase):
    """変換が残す**5本目** ── 機側データ集計の材料

    4本組は日報管理ツールが持っている欄だけです(停止は**記号**で、内訳名は
    ありません)。それだけを材料にすると、**同じシートなのに集計の答えが
    変わります**(ブックから集計すると内訳側、CSVから集計すると記号側)。
    シートから直に拾ったものを一緒に残せば、どちらから集計しても同じです。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def convert(self, cells: dict | None = None, *, name: str = "2026年8月23日_機側"):
        from converter import run
        from tests._fixture import tally_sheet

        books = self.tmp / "books"
        books.mkdir(parents=True, exist_ok=True)
        body = {8: {"D": "L1", "L": "9", "M": "0", "J": "8", "K": "0",
                    "I": "12", "Y": "10", "Z": "1000", "AC": "60",
                    "S": "材料待ち", "T": "30", "AJ": "ヲ", "AK": "30"}}
        sheet = tally_sheet(cells or body, E1="2026年8月23日", E2="機側",
                            AA8="作業者A")
        write_xlsx(books / "a.xlsx", sheet, name=name)
        out = self.tmp / "out"
        run.convert([books], out, line="LS")
        return out

    def test_日付フォルダに材料も残す(self) -> None:
        out = self.convert()
        made = list(out.rglob("機側データ集計_*.csv"))
        self.assertEqual(len(made), 1, [p.name for p in made])
        self.assertEqual(made[0].name, "機側データ集計_LS_2026.08.23.csv")
        # 4本組と同じ日付フォルダに置きます(フォルダごとドラッグできるように)
        self.assertTrue((made[0].parent / "集計明細_2026-08-23_LS.csv").is_file())

    def test_材料から集計するとブックと同じ(self) -> None:
        """**ここが肝です。** シートA → CSVA → 集計A が、シートA → 集計A と同じ。"""
        from converter import tally

        out = self.convert()
        book = next((self.tmp / "books").glob("*.xlsx"))
        folder = sorted(next(out.rglob("機側データ集計_*.csv")).parent.glob("*.csv"))
        self.assertEqual(tally.build([book]).rows, tally.build(folder).rows)

    def test_集計明細と二重に数えない(self) -> None:
        """日付フォルダには材料と集計明細の両方が入っています。"""
        from converter import tally

        out = self.convert()
        folder = sorted(next(out.rglob("機側データ集計_*.csv")).parent.glob("*.csv"))
        found = tally.build(folder)
        self.assertEqual(len(found.rows), 1)
        skipped = [s for s in found.sheets if s.kind == "CSV(集計明細)"]
        self.assertTrue(skipped[0].skipped)
        self.assertIn("二重に数えない", skipped[0].skipped)

    def test_材料が拾えなくても変換は止まらない(self) -> None:
        """停止2枠など、集計の材料にならないシートもあります。"""
        from tests._fixture import day_sheet

        books = self.tmp / "books2"
        books.mkdir(parents=True, exist_ok=True)
        write_xlsx(books / "a.xlsx", day_sheet())     # A1が「集計」でない
        from converter import run
        out = self.tmp / "out2"
        found = run.convert([books], out, line="HVC")
        self.assertTrue(found.files[0].ok, found.files[0].error)
        self.assertEqual(found.files[0].tally_rows, 0)
        self.assertEqual(list(out.rglob("機側データ集計_*.csv")), [])
        self.assertTrue(list(out.rglob("集計明細_*.csv")))


class StopNameTests(unittest.TestCase):
    """停止の「内訳」名 ── **停止内訳CSVがあれば、マスタより先に読みます。**

    移行元(日報管理ツール)が v3.93.0 で変えた仕組みをそのまま使っています
    (`nippou/logic/stop_csv.py`)。管理者の鍵が要る伝送用ファイルを直さずに、
    メモ帳で停止理由を足せるようにするためのものです。
    """

    CSV = ("分類,内訳番号,内訳,備考\r\n"
           "1,0,休憩食事,\r\n2,タ,材料待ち,\r\n3,G,段取り変更,\r\n")

    def setUp(self) -> None:
        from converter import user_settings

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # **後のテストに持ち越さない**(向け先は環境変数と設定ファイル)
        self.addCleanup(user_settings.apply_to_vendor, user_settings.Settings())

    def convert_with_csv(self, text: str = "") -> list[dict]:
        from converter import run, user_settings

        folder = self.tmp / "停止"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "停止内訳.csv").write_text(text or self.CSV,
                                             encoding="utf-8-sig")
        user_settings.apply_to_vendor(
            user_settings.Settings(stop_csv=str(folder)))

        books = self.tmp / "books"
        books.mkdir(parents=True, exist_ok=True)
        write_xlsx(books / "a.xlsx", day_sheet())
        out = self.tmp / "out"
        run.convert([books], out, line="HVC")
        made = next(out.rglob("停止内訳_*.csv"))
        with open(made, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_CSVの内訳名が入る(self) -> None:
        rows = self.convert_with_csv()
        names = {row["記号"]: row["内訳"] for row in rows}
        self.assertEqual(names.get("0"), "休憩食事")
        self.assertEqual(names.get("タ"), "材料待ち")
        self.assertEqual(names.get("G"), "段取り変更")

    def test_CSVに無い記号は空のまま通す(self) -> None:
        """**記号と分類は出ます。** 空になるのは内訳の1列だけです。"""
        rows = self.convert_with_csv("分類,内訳番号,内訳,備考\r\n1,0,休憩食事,\r\n")
        names = {row["記号"]: row["内訳"] for row in rows}
        self.assertEqual(names.get("0"), "休憩食事")
        self.assertEqual(names.get("タ"), "")
        self.assertTrue(all(row["分類"] for row in rows))

    def test_読めぐあいを言える(self) -> None:
        from converter import stop_reasons, user_settings

        folder = self.tmp / "停止"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "停止内訳.csv").write_text(self.CSV, encoding="utf-8-sig")
        user_settings.apply_to_vendor(
            user_settings.Settings(stop_csv=str(folder)))
        ok, text = stop_reasons.notice()
        self.assertTrue(ok, text)
        self.assertIn("3記号", text)

    def read_status(self, text: str):
        from converter import stop_reasons, user_settings

        folder = self.tmp / "停止"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "停止内訳.csv").write_text(text, encoding="utf-8-sig")
        user_settings.apply_to_vendor(
            user_settings.Settings(stop_csv=str(folder)))
        return stop_reasons.read()

    def test_名前の空の99は直せと言わない(self) -> None:
        """**分類1・内訳番号99 だけは名前が空でよい**(日報管理ツールの決まり)。

        写し(v3.96.0)はこの行を「内訳(理由の名前)がありません」と言って
        捨てるので、変換のたびに直せと言われていました。
        """
        for row in ("1,99,,", "1,99", "1,99,", "1,９９,,", "設備停止,99,,"):
            with self.subTest(row=row):
                status = self.read_status(self.CSV + row + "\r\n")
                self.assertTrue(status.ok, status.text())
                self.assertEqual(status.problems, [])
                self.assertNotIn("直すところ", status.text())

    def test_ほかの名前の空は今までどおり言う(self) -> None:
        """空でよいのは**分類1 の 99 だけ**です(2 の 99・1 の 98 は言います)。"""
        for row in ("2,99,,", "1,98,,", "3,99,,"):
            with self.subTest(row=row):
                status = self.read_status(self.CSV + row + "\r\n")
                self.assertEqual(len(status.problems), 1, status.problems)
                self.assertIn("直すところ", status.text())

    def test_名前の空の99があっても変換の内訳は空で出る(self) -> None:
        rows = self.convert_with_csv(self.CSV + "1,99,,\r\n")
        names = {row["記号"]: row["内訳"] for row in rows}
        self.assertEqual(names.get("0"), "休憩食事")      # ほかの行は今までどおり


class VerifyTests(unittest.TestCase):
    """検算 ── **座標が合っていたかを、シート自身の合計で確かめる。**"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_one(self, **kw):
        write_xlsx(self.tmp / "a.xlsx", day_sheet(**kw))
        found = run.convert([self.tmp], self.tmp / "out", line="HVC",
                            dry_run=True)
        return found.files[0]

    def test_合っていれば通る(self) -> None:
        one = self.run_one()
        self.assertTrue(one.verify.ok, one.verify.summary())
        self.assertIn("すべて一致", one.verify.summary())

    def test_合わなければ名指しする(self) -> None:
        """**黙って通しません。** 座標がずれても数は出てしまいます。"""
        one = self.run_one(stops_ok=False)
        self.assertFalse(one.verify.ok)
        self.assertTrue(one.verify.bad)
        self.assertIn("停止", one.verify.summary())

    def test_直ごとに見る(self) -> None:
        """どの直がずれたかまで分かるように。"""
        one = self.run_one()
        what = [line.what for line in one.verify.lines]
        for shift in ("1直", "2直", "3直"):
            self.assertIn(f"{shift} 枚数", what)
            self.assertIn(f"{shift} 重量", what)
        self.assertIn("日計 枚数", what)
        self.assertIn("停止 合計(分)", what)

    def test_停止は振り分け後を数える(self) -> None:
        """**振り分け前(AJ列から右)は数えません。**

        記号のまま先に書き込む作業用の欄で(`AJ4` に「振り分け前」)、
        書き損じも入り得ます。シート自身の合計(T4/V4/X4)も振り分け後
        (S〜X)を数えているので、こちらと突き合わせないと別のものを
        比べることになります。
        """
        from converter import totals

        cells = day_sheet()
        # 振り分け前だけを狂わせる(用途名が紛れ込んだ、などの書き損じ)
        cells["AK8"] = "999"
        cells["AL8"] = "ヨ"
        cells["AM8"] = "888"
        write_xlsx(self.tmp / "a.xlsx", cells)
        found = run.convert([self.tmp], self.tmp / "out", line="HVC",
                            dry_run=True)
        one = found.files[0]
        self.assertTrue(one.verify.ok, one.verify.summary())
        line = [l for l in one.verify.lines if l.what.startswith("停止")][0]
        self.assertEqual(line.read, 60)            # 30 + 20 + 10(振り分け後)
        self.assertEqual(line.sheet, 60)

    def test_振り分け後の時間だけを足す(self) -> None:
        from converter import totals

        rows = {8: {"T": "30", "AK": "999"}, 9: {"V": "20"}, 10: {"X": "10"},
                151: {"T": "5"}, 152: {"T": "100"}}   # 152行目は明細の外
        self.assertEqual(totals.stop_minutes(rows), 65)

    def test_シートが持っていない合計は比べない(self) -> None:
        """**空欄を0として比べると、読み取りが合っているのに×になります。**

        実物の2025年のブックは T4/V4/X4(停止の合計)が空のままでした。
        空欄を0とみなすと「停止 合計(分): 読んだ数 60 / シート 0 ×」。
        読めているのにずれていると言うのは、いちばん悪い嘘です。
        """
        one = self.run_one(stop_totals=False)
        self.assertTrue(one.verify.ok, one.verify.summary())
        self.assertIn("停止 合計(分)", one.verify.skipped)
        self.assertNotIn("停止 合計(分)",
                         [line.what for line in one.verify.lines])
        self.assertIn("比べていない項目", one.verify.summary())

    def test_シートの日計が古ければシートの食い違いとして言う(self) -> None:
        """直合計は合っているのに日計だけ古いブックがありました。

        **責めるべきは読み取りではなくシート。** 直合計の足し算と比べ、
        食い違いは言っておきます(黙って直合計に差し替えません)。
        """
        one = self.run_one(day_stale=True)
        self.assertTrue(one.verify.ok, one.verify.summary())
        self.assertTrue(one.verify.notes)
        self.assertIn("食い違っています", one.verify.summary())
        self.assertIn("合計式", one.verify.summary())

    def test_合計が1つも無ければ丸にしない(self) -> None:
        one = self.run_one(no_totals=True)
        self.assertTrue(one.verify.no_totals)
        self.assertFalse(one.verify.ok)


class CheckLogicTests(unittest.TestCase):
    """突き合わせの決まり(`converter/check.py`)。**純粋な部分。**"""

    def test_許しの幅(self) -> None:
        """重量は小数第1位まで持つので、丸めのぶんは許します。"""
        self.assertTrue(check.Line("重量", 1000.04, 1000.0,
                                   check.WEIGHT_TOLERANCE).ok)
        self.assertFalse(check.Line("重量", 1000.5, 1000.0,
                                    check.WEIGHT_TOLERANCE).ok)

    def test_差を書く(self) -> None:
        line = check.Line("1直 枚数", 78, 80)
        self.assertIn("78", line.text())
        self.assertIn("80", line.text())
        self.assertIn("-2", line.text())

    def test_合計が無ければ丸にしない(self) -> None:
        """**確かめていないことを、確かめたことにしない。**"""
        report = check.build("x", read_by_shift={"1直": (5.0, 100.0)},
                             read_stops=0.0, sheet_by_shift={},
                             sheet_day=(None, None), sheet_stops={})
        self.assertTrue(report.no_totals)
        self.assertFalse(report.ok)
        self.assertIn("突き合わせられません", report.summary())

    def test_空欄と0は別のもの(self) -> None:
        """シートの0は**0として比べます**。空欄は比べません。"""
        zero = check.build("x", read_by_shift={}, read_stops=60.0,
                           sheet_by_shift={}, sheet_day=(None, None),
                           sheet_stops={"管理ロス停止": 0.0, "突発停止": 0.0,
                                        "ハンドリング停止": 0.0})
        self.assertFalse(zero.ok)
        self.assertIn("停止", zero.summary())

        empty = check.build("x", read_by_shift={"1直": (5.0, 100.0)},
                            read_stops=60.0,
                            sheet_by_shift={"1直": (5.0, 100.0)},
                            sheet_day=(None, None),
                            sheet_stops={"管理ロス停止": None, "突発停止": None,
                                         "ハンドリング停止": None})
        self.assertTrue(empty.ok, empty.summary())
        self.assertIn("停止 合計(分)", empty.skipped)

    def test_停止が3つ揃っていなければ比べない(self) -> None:
        """**足せた分だけ比べると嘘になります**(1つ欠けたら比べない)。"""
        report = check.build("x", read_by_shift={"1直": (5.0, 100.0)},
                             read_stops=60.0,
                             sheet_by_shift={"1直": (5.0, 100.0)},
                             sheet_day=(5.0, 100.0),
                             sheet_stops={"管理ロス停止": 30.0, "突発停止": 30.0,
                                          "ハンドリング停止": None})
        self.assertTrue(report.ok, report.summary())
        self.assertIn("停止 合計(分)", report.skipped)

    def test_日計が空なら日計は比べない(self) -> None:
        """**直合計で済ませて「日計も合った」とは言いません。**

        直合計の足し算と比べれば○は出せますが、それは直合計の行で
        すでに確かめたことです。シートが持っていないものを持っていた
        ことにはしません ── 比べていないと言います。
        """
        report = check.build("x", read_by_shift={"1直": (5.0, 100.0),
                                                "2直": (7.0, 200.0)},
                             read_stops=0.0,
                             sheet_by_shift={"1直": (5.0, 100.0),
                                             "2直": (7.0, 200.0)},
                             sheet_day=(None, None), sheet_stops={})
        self.assertTrue(report.ok, report.summary())
        self.assertIn("日計 枚数", report.skipped)
        self.assertIn("日計 重量", report.skipped)
        self.assertFalse(report.notes)

    def test_読めない字は0として扱う(self) -> None:
        self.assertEqual(check.to_number("あ"), 0.0)
        self.assertEqual(check.to_number(None), 0.0)
        self.assertEqual(check.to_number(" 12.5 "), 12.5)


class TotalsTests(unittest.TestCase):
    """合計欄の拾い方(`converter/totals.py`)。**空欄は `None`。**"""

    def test_空欄と読めない字は無しにする(self) -> None:
        self.assertIsNone(totals.number_or_none(""))
        self.assertIsNone(totals.number_or_none(None))
        self.assertIsNone(totals.number_or_none("  "))
        self.assertIsNone(totals.number_or_none("#VALUE!"))
        self.assertEqual(totals.number_or_none("0"), 0.0)
        self.assertEqual(totals.number_or_none(" 12.5 "), 12.5)

    def test_シートから拾う(self) -> None:
        rows = {3: {"AA": "42", "AB": "4200.5"},
                4: {"T": "30", "V": "20"},            # X4 は空
                55: {"AA": "30", "AB": "3000"},
                103: {"AA": "5", "AB": "500.5"},
                151: {"AA": "7", "AB": "700"}}
        found = totals.read(rows)
        self.assertEqual(found.day, (42.0, 4200.5))
        self.assertEqual(found.by_shift["1直"], (30.0, 3000.0))
        self.assertEqual(found.stops["管理ロス停止"], 30.0)
        self.assertIsNone(found.stops["ハンドリング停止"])
        self.assertFalse(found.nothing)

    def test_1つも無いときは無しと言える(self) -> None:
        self.assertTrue(totals.read({}).nothing)


class CommandTests(unittest.TestCase):
    """コマンドとして動くか。**追加ライブラリ無しで。**"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def call(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "convert.py"), *args],
            capture_output=True, text=True)

    def test_走ってCSVが出る(self) -> None:
        write_xlsx(self.tmp / "a.xlsx", day_sheet())
        done = self.call(str(self.tmp), "--out", str(self.tmp / "out"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("書いたCSV", done.stdout)
        self.assertTrue(list((self.tmp / "out").rglob("*.csv")))

    def test_読む相手が無ければ断る(self) -> None:
        done = self.call(str(self.tmp), "--out", str(self.tmp / "out"))
        self.assertEqual(done.returncode, 1)
        self.assertIn("見つかりません", done.stderr)

    def test_数が合わなければ2で返る(self) -> None:
        """**押し黙って0を返さない。** 続きの処理が気づけるように。"""
        write_xlsx(self.tmp / "a.xlsx", day_sheet(stops_ok=False))
        done = self.call(str(self.tmp), "--out", str(self.tmp / "out"))
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("数が合わない", done.stdout)

    def test_読めなければ1で返る(self) -> None:
        (self.tmp / "a.xlsx").write_bytes("xlsx ではありません".encode("utf-8"))
        done = self.call(str(self.tmp), "--out", str(self.tmp / "out"))
        self.assertEqual(done.returncode, 1)
        self.assertIn("読めなかった", done.stdout)


class NoExtraLibraryTests(unittest.TestCase):
    """**追加ライブラリを増やさない。** 現場のPCへ配るものなので。"""

    def test_取り込んでいない(self) -> None:
        import re

        pattern = re.compile(r"^\s*(?:import|from)\s+(\w+)", re.M)
        names = set()
        for source in ROOT.rglob("*.py"):
            names.update(pattern.findall(source.read_text(encoding="utf-8")))
        for banned in ("openpyxl", "xlrd", "pandas", "numpy", "flask"):
            self.assertNotIn(banned, names, f"{banned} が入っている")


if __name__ == "__main__":
    unittest.main()
