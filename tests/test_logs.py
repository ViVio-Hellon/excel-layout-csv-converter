"""ログ ── **あとから追えること**(基盤仕様書 2.6)

「エラーの後追いができない」という声への答えなので、見るのは3つです:

    置き場所  設定で変えられて、書けないときは黙って消えないこと
    名前      **どのツールが出したものか、開かなくても分かること**
              (共有の掃き出し先は何本ものツールで共用されます)
    中身      うまくいかなかったことが、理由と中身(traceback)つきで残ること
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

from converter import app_config, logging_utils     # noqa: E402


class LogFolderTests(unittest.TestCase):
    """置き場所 ── 設定で変えられる。**書けなければ戻す。**"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # **持ち越さない。** 置き場所はプロセス全体のものなので
        self.addCleanup(logging_utils.use_folder, "")

    def test_指した場所へ出す(self) -> None:
        """**ツール名のフォルダを作って、その中へ出します。**

        共有の掃き出し先には `dbviewer` `rneconverter` `nippou` …と
        何本ものツールのログが集まります。混ざらないように1段作ります。
        """
        where = self.tmp / "共有ログ"
        self.assertEqual(logging_utils.use_folder(str(where)), "")
        tool = logging_utils.tool_name()
        self.assertEqual(logging_utils.folder(), where / tool)

        logging_utils.get_logger("試し").info("ここに出ます")
        self.assertIn("ここに出ます",
                      (where / tool / f"{tool}_app.log")
                      .read_text(encoding="utf-8"))

    def test_名前がツール名から始まる(self) -> None:
        """**1本取り出したときに、開かなくても分かるように。**"""
        logging_utils.use_folder(str(self.tmp / "共有ログ"))
        tool = logging_utils.tool_name()
        logging_utils.get_logger("試し").info("筋道")
        logging_utils.get_logger("launcher").warning("起動のこと")
        made = sorted(p.name for p in logging_utils.folder().glob("*.log"))
        self.assertEqual(made, [f"{tool}_app.log", f"{tool}_error.log",
                                f"{tool}_launcher.log"])
        for name in made:
            self.assertTrue(name.startswith(tool + "_"), name)

    def test_指した先がすでにツール名なら重ねない(self) -> None:
        """`…\ログ\ExcelCsvConverter` を指されても1段だけ。"""
        tool = logging_utils.tool_name()
        where = self.tmp / "ログ" / tool
        logging_utils.use_folder(str(where))
        self.assertEqual(logging_utils.folder(), where)

    def test_ツール名は設定から採る(self) -> None:
        """現場の呼び名に合わせられるように(`config/app.json` の `log_name`)。"""
        self.assertEqual(logging_utils.tool_name(), app_config.log_name())
        self.assertNotIn("/", logging_utils.tool_name())
        self.assertNotIn("\\", logging_utils.tool_name())

    def test_書けない場所ならローカルへ戻す(self) -> None:
        """**黙って消えるのがいちばん困ります。** 戻した理由も残します。"""
        blocker = self.tmp / "これはファイル"
        blocker.write_text("", encoding="utf-8")
        why = logging_utils.use_folder(str(blocker / "下"))
        self.assertTrue(why)
        self.assertIn("ローカル領域", why)
        self.assertEqual(logging_utils.folder(), app_config.local_dir("logs"))
        self.assertEqual(logging_utils.note(), why)
        self.assertIn(why, (app_config.local_dir("logs")
                            / logging_utils.file_name(logging_utils.ERROR_LOG))
                      .read_text(encoding="utf-8"))

    def test_空に戻すとローカル(self) -> None:
        logging_utils.use_folder(str(self.tmp / "どこか"))
        logging_utils.use_folder("")
        self.assertEqual(logging_utils.folder(), app_config.local_dir("logs"))

    def test_どこを見ればよいかを返す(self) -> None:
        where = self.tmp / "ログ"
        logging_utils.use_folder(str(where))
        tool = logging_utils.tool_name()
        found = logging_utils.log_paths()
        self.assertEqual(found["folder"], str(where / tool))
        self.assertEqual(found["tool"], tool)
        self.assertEqual(found["error"], str(where / tool / f"{tool}_error.log"))
        self.assertEqual(found["note"], "")
        self.assertEqual(found["default"], str(app_config.local_dir("logs")))

    def test_すでに作ったログも向き直す(self) -> None:
        """**起動のあとに設定を変えても効きます**(作り直しません)。"""
        log = logging_utils.get_logger("向き直し")
        where = self.tmp / "あとから"
        logging_utils.use_folder(str(where))
        log.info("新しい場所へ")
        self.assertIn("新しい場所へ",
                      (logging_utils.folder() / logging_utils.file_name(
                          logging_utils.APP_LOG))
                      .read_text(encoding="utf-8"))


class ErrorLogTests(unittest.TestCase):
    """中身 ── `error.log` は**うまくいかなかったことだけ**。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(logging_utils.use_folder, "")
        logging_utils.use_folder(str(self.tmp / "ログ"))
        self.where = logging_utils.folder()        # ツール名のフォルダまで
        self.tool = logging_utils.tool_name()

    def test_普通の知らせは入らない(self) -> None:
        logging_utils.get_logger("選り分け").info("ふつうの筋道")
        text = (self.where / f"{self.tool}_error.log").read_text(encoding="utf-8")
        self.assertNotIn("ふつうの筋道", text)
        self.assertIn("ふつうの筋道",
                      (self.where / f"{self.tool}_app.log").read_text(encoding="utf-8"))

    def test_警告と例外は中身つきで残る(self) -> None:
        """**「読めませんでした」だけでは、なぜに進めません。**"""
        log = logging_utils.get_logger("失敗")
        log.warning("数が合いません 1直 枚数: 読んだ数 62 / シート 28")
        try:
            raise ValueError("作業日(E1)が読めません: 2025/7/15")
        except ValueError as exc:
            log.exception("読めませんでした: %s", exc)
        text = (self.where / f"{self.tool}_error.log").read_text(encoding="utf-8")
        self.assertIn("数が合いません", text)
        self.assertIn("作業日(E1)が読めません", text)
        self.assertIn("Traceback", text)           # 中身まで

    def test_起動のログは別の1本(self) -> None:
        logging_utils.get_logger("launcher").info("起動します")
        self.assertIn("起動します",
                      (self.where / f"{self.tool}_launcher.log").read_text(encoding="utf-8"))


class JobTraceTests(unittest.TestCase):
    """仕事の番号で、**頭から終わりまで**追えること。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(logging_utils.use_folder, "")
        logging_utils.use_folder(str(self.tmp / "ログ"))
        logging.getLogger("nippou").setLevel(logging.CRITICAL)

    def test_1本ごとの筋道が残る(self) -> None:
        from converter import run
        from tests._fixture import day_sheet, write_xlsx

        books = self.tmp / "books"
        books.mkdir()
        write_xlsx(books / "a.xlsx", day_sheet())
        (books / "こわれ.xlsx").write_bytes("xlsx ではありません".encode("utf-8"))
        run.convert([books], self.tmp / "out", line="HVC", tag="ab12cd34")

        where = logging_utils.folder()
        tool = logging_utils.tool_name()
        app = (where / f"{tool}_app.log").read_text(encoding="utf-8")
        for must in ("[job=ab12cd34] 変換開始", "読む相手:",
                     "1/2 a.xlsx 開きます", "シート=", "読めました",
                     "こわれ.xlsx 読めませんでした", "変換おわり"):
            with self.subTest(must=must):
                self.assertIn(must, app)

        error = (where / f"{tool}_error.log").read_text(encoding="utf-8")
        self.assertIn("ab12cd34", error)           # 番号から app.log を追える
        self.assertIn("Traceback", error)


if __name__ == "__main__":
    unittest.main()
