"""設定部 ── 入力元・出力先・ライン・参照用マスタ

**「出たはずなのに見当たらない」を作らない**ための層なので、どこを指して
いるかを返すことと、足りないまま始めないことを見ます。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._web import WebCase                     # noqa: E402


class SettingsTests(WebCase):
    def test_保存して読み直せる(self) -> None:
        screen_id = self.claim()
        data = self.settings(screen_id,
                             output_dir='"' + str(self.tmp / "out") + '"',
                             line="LS")
        # 引用符つきで貼られても、そのまま使える形に直す
        self.assertEqual(data["settings"]["output_dir"], str(self.tmp / "out"))
        self.assertEqual(data["message"], "保存しました")
        _status, again = self.request("GET", "/api/settings", screen_id=screen_id)
        self.assertEqual(again["settings"]["line"], "LS")

    def test_どこを指しているか返す(self) -> None:
        screen_id = self.claim()
        data = self.settings(screen_id, input_dir=str(self.books()))
        found = {p["key"]: p for p in data["paths"]}
        self.assertTrue(found["input_dir"]["exists"])
        self.assertFalse(found["output_dir"]["exists"])

    def test_出力先が無ければ始めない(self) -> None:
        screen_id = self.claim()
        self.settings(screen_id, input_dir=str(self.books()))
        status, data = self.request("POST", "/api/job/start", {"source": "folder"},
                                    screen_id=screen_id)
        self.assertEqual(status, 400)
        self.assertIn("出力先", data["detail"])

    def test_入力フォルダが無ければ始めない(self) -> None:
        screen_id = self.claim()
        self.settings(screen_id, input_dir=str(self.tmp / "ない"),
                      output_dir=str(self.tmp / "out"))
        status, data = self.request("POST", "/api/job/start", {"source": "folder"},
                                    screen_id=screen_id)
        self.assertEqual(status, 400)
        self.assertIn("入力フォルダ", data["detail"])

    def test_知らない場所は開かない(self) -> None:
        """画面から渡された道をそのまま開くと、任意の場所を開かせる隙になる。"""
        screen_id = self.claim()
        status, _data = self.request("POST", "/api/open", {"what": "C:\\Windows"},
                                     screen_id=screen_id)
        self.assertEqual(status, 400)

    def test_設定は端末のローカル領域に残る(self) -> None:
        from converter import app_config, user_settings

        screen_id = self.claim()
        self.settings(screen_id, line="HVC")
        saved = app_config.local_dir("data") / "settings.json"
        self.assertTrue(saved.is_file())
        self.assertEqual(user_settings.load().line, "HVC")


class StopReasonsTests(WebCase):
    """停止内訳 ── **出どころが2つ**(停止内訳CSV と 参照用マスタ)。

    記号と分類はマスタが無くても出ます(分類は記号の字種で決まる)。
    読めないと空になるのは「内訳」の1列だけなので、**変換は止めません**。
    どちらから読んだかは画面に出します ── 言わないと「名前が空のまま」の
    理由が分からなくなります。
    """

    CSV = ("# 覚え書き\r\n分類,内訳番号,内訳,備考\r\n"
           "1,0,休憩食事,\r\n2,イ,突発停止(機械),\r\n3,E,段取り変更,\r\n")

    def stop_csv(self, where, text: str = "") -> object:
        from pathlib import Path

        folder = Path(where)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "停止内訳.csv"
        path.write_text(text or self.CSV, encoding="utf-8-sig")
        return path

    # -- 道の決め方 --------------------------------------------------
    def test_フォルダでもファイルでも受ける(self) -> None:
        from converter import user_settings

        folder = self.tmp / "停止"
        made = self.stop_csv(folder)
        for text in (str(folder), str(made)):
            with self.subTest(text=text):
                found = user_settings.stop_csv_path(
                    user_settings.Settings(stop_csv=text))
                self.assertEqual(found, made)

    def test_空なら写し側の既定にまかせる(self) -> None:
        from converter import user_settings

        self.assertIsNone(user_settings.stop_csv_path(user_settings.Settings()))

    def test_どこを指しているか画面に返す(self) -> None:
        screen_id = self.claim()
        made = self.stop_csv(self.tmp / "停止")
        data = self.settings(screen_id, stop_csv=str(self.tmp / "停止"))
        found = {p["key"]: p for p in data["paths"]}
        self.assertEqual(found["stop_csv"]["resolved"], str(made))
        self.assertTrue(found["stop_csv"]["exists"])

    def test_まだ無いファイルでも断らない(self) -> None:
        """**これから置く**場合があります。マスタの表を読むだけです。"""
        screen_id = self.claim()
        data = self.settings(screen_id, stop_csv=str(self.tmp / "まだ"))
        found = {p["key"]: p for p in data["paths"]}
        self.assertFalse(found["stop_csv"]["exists"])
        self.assertIn("まだありません", found["stop_csv"]["note"])

    # -- 読めぐあい --------------------------------------------------
    def test_CSVから読めたら出どころを言う(self) -> None:
        screen_id = self.claim()
        self.stop_csv(self.tmp / "停止")
        data = self.settings(screen_id, stop_csv=str(self.tmp / "停止"))
        found = data["stop_reasons"]
        self.assertTrue(found["ok"], found)
        self.assertEqual(found["codes"], 3)
        self.assertEqual(set(found["origins"].values()), {"CSV"})
        self.assertIn("停止内訳", found["text"])

    def test_直すところは行番号つきで出す(self) -> None:
        """**黙って捨てません。** 捨てると一覧から理由がひっそり消えます。"""
        screen_id = self.claim()
        self.stop_csv(self.tmp / "停止",
                      "分類,内訳番号,内訳,備考\r\n"
                      "1,0,休憩食事,\r\n9,X,知らない分類,\r\n")
        data = self.settings(screen_id, stop_csv=str(self.tmp / "停止"))
        found = data["stop_reasons"]
        self.assertTrue(any("3行目" in p for p in found["problems"]), found)
        self.assertIn("直すところ", found["text"])

    def test_読めなければ何が空になるか言う(self) -> None:
        screen_id = self.claim()
        data = self.settings(screen_id, stop_csv=str(self.tmp / "ない"))
        found = data["stop_reasons"]
        self.assertFalse(found["ok"])
        self.assertIn("内訳", found["text"])
        self.assertIn("記号と分類は出ます", found["text"])

    def test_写し側へ渡す(self) -> None:
        """向こうは**この端末の設定**から読みます(`stop_reason_file`)。"""
        from nippou import user_settings as theirs

        screen_id = self.claim()
        made = self.stop_csv(self.tmp / "停止")
        self.settings(screen_id, stop_csv=str(self.tmp / "停止"))
        self.assertEqual(theirs.get("stop_reason_file"), str(made))
        # 空にしたら**消えます**(前の場所を読み続けない)
        self.settings(screen_id, stop_csv="")
        self.assertFalse(theirs.get("stop_reason_file"))


class LogSettingTests(WebCase):
    """ログの置き場所 ── **設定から変えられること。**"""

    def setUp(self) -> None:
        super().setUp()
        from converter import logging_utils
        self.addCleanup(logging_utils.use_folder, "")

    def test_保存するとそこへ出る(self) -> None:
        from converter import logging_utils

        screen_id = self.claim()
        where = self.tmp / "共有ログ"
        data = self.settings(screen_id, log_dir=str(where))
        # **指した先にツール名のフォルダを作り、その中へ出します** ──
        # 共有の掃き出し先は何本ものツールで共用されるので
        tool = logging_utils.tool_name()
        self.assertEqual(data["logs"]["folder"], str(where / tool))
        self.assertEqual(logging_utils.folder(), where / tool)
        self.assertTrue((where / tool / f"{tool}_app.log").is_file())

    def test_どこを指しているか返す(self) -> None:
        screen_id = self.claim()
        data = self.settings(screen_id, log_dir=str(self.tmp / "ログ"))
        found = {p["key"]: p for p in data["paths"]}
        self.assertEqual(found["log_dir"]["resolved"], str(self.tmp / "ログ"))

    def test_書けない場所なら断りを返す(self) -> None:
        """**黙って消しません。** 画面にも理由を出します。"""
        screen_id = self.claim()
        blocker = self.tmp / "ファイルです"
        blocker.write_text("", encoding="utf-8")
        data = self.settings(screen_id, log_dir=str(blocker / "下"))
        self.assertIn("ローカル領域", data["logs"]["note"])
