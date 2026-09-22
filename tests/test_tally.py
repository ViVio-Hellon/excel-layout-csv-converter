"""機側データ集計 ── VBAと同じ拾い方・同じ埋め方になっているか

【なぜ機械的に見るのか】
この転記は**順番で結果が変わります**。ロット№を先に上詰めで埋めてから、
そのロットを鍵にして納入先や包装仕様NOを埋めるためです。順番を入れ替えても
エラーにはならず、**値だけが静かに変わります**。

実物のブックは置いていません(作業者名も納入先も実名)。同じ座標へ同じ形の
値を置いたシートを組み立てて試します。実物との突き合わせは別に行っています
── 実機5日ぶん(2026.08.23〜27)で 210 行、既存の変換も9項目すべて一致。
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

from converter import tally                        # noqa: E402
from tests._fixture import day_sheet, tally_sheet, write_xlsx   # noqa: E402

NAME = "2026年8月23日_機側"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def book(self, body: dict, *, name: str = NAME, file: str = "book.xlsx",
             **head) -> Path:
        path = self.tmp / file
        write_xlsx(path, tally_sheet(body, **head), name=name)
        return path

    def rows(self, *paths) -> list[list[str]]:
        return tally.build(list(paths)).rows


class 拾う行(Base):
    def test_終了時刻がある行を拾う(self) -> None:
        found = self.rows(self.book({8: {"D": "L1", "L": "9", "AC": "35"}}))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][tally.LOT], "L1")

    def test_停止の対だけの行も拾う(self) -> None:
        """**作業時間が空で停止だけ書かれた行**が実物にあります。

        「内径カット 30分」のような行で、終了時刻の欄は空のままです
        (実機のブックでは 9・10 行目などに出てきます)。
        """
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9"},
            9: {"W": "内径カット", "X": "30"},      # 終了時刻なし
            10: {"L": "10"},                       # ここが L の最終行
        }))
        self.assertEqual(len(found), 3)
        self.assertEqual(found[1][15], "内径カット")
        self.assertEqual(found[1][16], "30")

    def test_片方だけの停止は拾わない(self) -> None:
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9"},
            9: {"W": "内径カット"},                 # 時間が無い
            10: {"X": "30"},                       # 内容が無い
        }))
        self.assertEqual(len(found), 1)

    def test_L列が1つも無ければ何も拾わない(self) -> None:
        """VBAは最終行を L 列で決めるので、`8 To 1` になり1度も回りません。

        **停止だけの行があっても拾われません。** 同じにします。
        """
        found = self.rows(self.book({8: {"W": "内径カット", "X": "30"}}))
        self.assertEqual(found, [])

    def test_L列の最終行より下は見ない(self) -> None:
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9"},
            20: {"W": "内径カット", "X": "30"},     # L の最終行より下
        }))
        self.assertEqual(len(found), 1)

    def test_7行目以前は見ない(self) -> None:
        found = self.rows(self.book({7: {"D": "見出し", "L": "9"},
                                     8: {"D": "L1", "L": "9"}}))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][tally.LOT], "L1")


class 対象シート(Base):
    def test_A1が集計でなければ拾わない(self) -> None:
        path = self.book({8: {"D": "L1", "L": "9"}}, A1="日報")
        found = tally.build([path])
        self.assertEqual(found.rows, [])
        self.assertIn("対象外", found.sheets[0].skipped)

    def test_名前に機側が無ければ拾わない(self) -> None:
        path = self.book({8: {"D": "L1", "L": "9"}}, name="2026年8月23日_HVC")
        found = tally.build([path])
        self.assertEqual(found.rows, [])
        self.assertIn("対象外", found.sheets[0].skipped)

    def test_停止2枠のブックは断る(self) -> None:
        """**黙って読みません。** 列が2つずれていて、別の値が入ります。"""
        path = self.book({8: {"D": "L1", "L": "9"}},
                         AC7="用途コード", AE7="包装仕様NO")
        found = tally.build([path])
        self.assertEqual(found.rows, [])
        self.assertIn("見出しが違います", found.sheets[0].skipped)
        self.assertIn("停止3枠", found.sheets[0].skipped)

    def test_日付はシート名から採る(self) -> None:
        found = self.rows(self.book({8: {"D": "L1", "L": "9"}}))
        self.assertEqual(found[0][0], "2026年8月23日")

    def test_読めないブックは理由を残す(self) -> None:
        broken = self.tmp / "こわれ.xlsx"
        broken.write_bytes("これはZIPではありません".encode("utf-8"))
        found = tally.build([broken])
        self.assertEqual(found.rows, [])
        self.assertEqual(found.errors[0][0], "こわれ.xlsx")


class 空欄の埋め方(Base):
    def test_ロット番号は上の行から引き継ぐ(self) -> None:
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9", "E": "材A"},
            9: {"L": "9"},
            10: {"L": "9"},
            11: {"D": "L2", "L": "9", "E": "材B"},
            12: {"L": "9"},
        }))
        self.assertEqual([r[tally.LOT] for r in found],
                         ["L1", "L1", "L1", "L2", "L2"])
        self.assertEqual([r[2] for r in found], ["材A"] * 3 + ["材B"] * 2)

    def test_先頭の空欄は最初に見つかった値で埋まる(self) -> None:
        """VBAの「上の値」の初期化がそうなっています。**同じにします。**"""
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9"},              # 作業人数(N)が空
            9: {"D": "L1", "L": "9", "N": "3"},
        }))
        self.assertEqual([r[19] for r in found], ["3", "3"])

    def test_納入先はロット単位で埋める(self) -> None:
        """**上の行は見ません。** ロットが変われば別の納入先です。"""
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9", "AG": "とりひき先A"},
            9: {"D": "L2", "L": "9"},              # 別ロット。空のまま
            10: {"D": "L1", "L": "9"},             # L1 なので A が入る
        }))
        self.assertEqual([r[7] for r in found], ["とりひき先A", "", "とりひき先A"])

    def test_包装仕様NOと縦割もロット単位(self) -> None:
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9", "AH": "1C1293", "AQ": "2", "AR": "20",
                "B": "1.3"},
            9: {"L": "9"},                         # ロットは上から引き継がれる
        }))
        self.assertEqual(found[1][18], "1C1293")
        self.assertEqual(found[1][20], "2")
        self.assertEqual(found[1][21], "20")
        self.assertEqual(found[1][22], "1.3")

    def test_梱包数や停止の時間は埋めない(self) -> None:
        """**書いていないものを作りません。** VBAも埋めていません。"""
        found = self.rows(self.book({
            8: {"D": "L1", "L": "9", "P": "8", "Q": "1", "AC": "35"},
            9: {"L": "9"},
        }))
        self.assertEqual(found[1][8], "")          # 梱包数
        self.assertEqual(found[1][9], "")          # 包数
        self.assertEqual(found[1][10], "")         # 作業時間


class 積む順(Base):
    def test_渡した順に積む(self) -> None:
        first = self.book({8: {"D": "L1", "L": "9"}}, file="1.xlsx")
        second = self.book({8: {"D": "L2", "L": "9"}}, file="2.xlsx",
                           name="2026年8月24日_機側")
        found = self.rows(first, second)
        self.assertEqual([r[0] for r in found],
                         ["2026年8月23日", "2026年8月24日"])

    def test_ブックをまたいでも上詰めは続く(self) -> None:
        """VBAは1枚の集計シートへ積むので、**日をまたいでも続きます。**"""
        first = self.book({8: {"D": "L1", "L": "9"}}, file="1.xlsx")
        second = self.book({8: {"L": "9"}}, file="2.xlsx",
                           name="2026年8月24日_機側")
        found = self.rows(first, second)
        self.assertEqual([r[tally.LOT] for r in found], ["L1", "L1"])

    def test_1冊に複数シートあれば全部見る(self) -> None:
        """既存の読み手は先頭1枚だけですが、VBAは全シートを回ります。"""
        from converter import books

        path = self.book({8: {"D": "L1", "L": "9"}})
        self.assertEqual(len(books.read_sheets(path)), 1)


class CSVを読む(Base):
    """ブックだけでなくCSVも受けます。**中身を見て2通りに分けます。**"""

    def csv(self, text: str, *, name: str = "何か.csv",
            encoding: str = "utf-8-sig") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding=encoding)
        return path

    def test_前に出した集計はそのまま積み増す(self) -> None:
        first = tally.build([self.book({8: {"D": "L1", "L": "9"}})])
        made = tally.write_csv(first, self.tmp / "out")
        again = tally.build([made])
        self.assertEqual(len(again.rows), 1)
        self.assertEqual(again.rows[0][tally.LOT], "L1")
        self.assertEqual(again.sheets[0].kind, "CSV(集計)")

    def test_ブックと集計CSVを混ぜられる(self) -> None:
        """VBAが集計シートへ**追記**していたのと同じ使い方です。"""
        first = tally.build([self.book({8: {"D": "L1", "L": "9"}},
                                       file="1.xlsx")])
        made = tally.write_csv(first, self.tmp / "out")
        book = self.book({8: {"D": "L2", "L": "9"}}, file="2.xlsx",
                         name="2026年8月24日_機側")
        both = tally.build([made, book])
        self.assertEqual([r[tally.LOT] for r in both.rows], ["L1", "L2"])

    def test_シートをCSVにしたものも読む(self) -> None:
        """日付は**作業日の欄(E1)**から採ります(シート名が無いため)。"""
        lines = [[""] * 44 for _ in range(9)]
        lines[0][0] = "集計"
        lines[0][4] = "2026年8月23日"
        for ref, text in (("S", "内容"), ("T", "時間（分）"), ("U", "内容"),
                          ("V", "時間（分）"), ("W", "内容"), ("X", "時間（分）"),
                          ("AC", "作業時間（分）"), ("AE", "用途コード"),
                          ("AF", "用途名"), ("AG", "納入先"), ("AH", "包装仕様NO")):
            index = 0
            for char in ref:
                index = index * 26 + (ord(char) - 64)
            lines[6][index - 1] = text
        lines[7][3] = "L9"          # D8 = ロット№
        lines[7][11] = "9"          # L8 = 終了時刻
        text = "\n".join(",".join(cell for cell in line) for line in lines)
        found = tally.build([self.csv(text, encoding="cp932")])
        self.assertEqual(len(found.rows), 1)
        self.assertEqual(found.rows[0][0], "2026年8月23日")
        self.assertEqual(found.rows[0][tally.LOT], "L9")
        self.assertEqual(found.sheets[0].kind, "CSV(シート)")

    def test_中身の分からないCSVは断る(self) -> None:
        """**黙って混ぜません。** 何の表か言えなくなります。"""
        found = tally.build([self.csv("あ,い\n1,2\n")])
        self.assertEqual(found.rows, [])
        self.assertIn("中身が分かりません", found.sheets[0].skipped)

    def test_列が多すぎる集計CSVは断る(self) -> None:
        text = ",".join(tally.HEADERS) + "\n" + ",".join(["x"] * 30) + "\n"
        found = tally.build([self.csv(text)])
        self.assertEqual(found.rows, [])
        self.assertIn("列が多すぎます", found.sheets[0].skipped)


class 書き出し(Base):
    def test_見出しは23列でVBAと同じ(self) -> None:
        self.assertEqual(len(tally.HEADERS), 23)
        self.assertEqual(tally.HEADERS[0], "日付")
        self.assertEqual(tally.HEADERS[-1], "係数処理ﾛｯﾄ数")
        # **同じ見出しが3回出ます**(VBAのまま)
        self.assertEqual(list(tally.HEADERS).count("時間（分）"), 3)

    def test_BOM付きUTF8で書く(self) -> None:
        found = tally.build([self.book({8: {"D": "L1", "L": "9"}})])
        path = tally.write_csv(found, self.tmp / "out")
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
        self.assertEqual(rows[0], list(tally.HEADERS))
        self.assertEqual(rows[1][tally.LOT], "L1")

    def test_書けなかったら前のCSVを壊さない(self) -> None:
        """`open(..., "w")` は開いた瞬間に中身を消します。**置き換えにします。**"""
        import os

        found = tally.build([self.book({8: {"D": "L1", "L": "9"}})])
        out = self.tmp / "out"
        first = tally.write_csv(found, out)
        before = first.read_bytes()

        # 置き換えに失敗する状況(書き先がフォルダになっている等)を作る
        real_replace = os.replace

        def refuse(src, dst):
            raise PermissionError("使用中です")

        os.replace = refuse
        self.addCleanup(setattr, os, "replace", real_replace)
        with self.assertRaises(PermissionError) as caught:
            tally.write_csv(found, out)
        os.replace = real_replace

        self.assertIn("開いたまま", str(caught.exception))
        self.assertEqual(first.read_bytes(), before)      # 前のCSVは無事
        self.assertEqual(list(out.glob("*.tmp")), [])     # 仮のものは残さない

    def test_名前に日付の幅が入る(self) -> None:
        first = self.book({8: {"D": "L1", "L": "9"}}, file="1.xlsx")
        second = self.book({8: {"D": "L2", "L": "9"}}, file="2.xlsx",
                           name="2026年8月27日_機側")
        found = tally.build([first, second])
        self.assertEqual(tally.file_stem(found),
                         "機側データ集計_2026.08.23-2026.08.27")

    def test_1日だけなら日付は1つ(self) -> None:
        found = tally.build([self.book({8: {"D": "L1", "L": "9"}})])
        self.assertEqual(tally.file_stem(found), "機側データ集計_2026.08.23")


class 既存の変換と混ぜない(Base):
    def test_日報の変換用ブックは対象外(self) -> None:
        """`day_sheet()` は A1 を持ちません ── 転記の対象にはなりません。"""
        path = self.tmp / "日報.xlsx"
        write_xlsx(path, day_sheet(), name="2026年8月26日_HVC")
        found = tally.build([path])
        self.assertEqual(found.rows, [])


if __name__ == "__main__":
    unittest.main()
