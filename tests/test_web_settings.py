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
