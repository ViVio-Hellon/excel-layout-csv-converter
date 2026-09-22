"""読ませるブックの受け取り ── ドラッグ投入とフォルダ指定

**100本まで。** 超えたぶんは黙って捨てず、断りを返します。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter import app_config, intake           # noqa: E402
from tests._fixture import day_sheet, write_xlsx   # noqa: E402
from tests._web import WebCase                     # noqa: E402


class IntakeNameTests(WebCase):
    def test_名前に区切り文字が入っていても外へ書かない(self) -> None:
        self.assertEqual(intake.safe_name(r"..\..\狙い.xlsx"), "狙い.xlsx")
        self.assertEqual(intake.safe_name("2026.08/機側.xlsx"), "機側.xlsx")


class InputRouteTests(WebCase):
    def _book(self) -> bytes:
        path = self.tmp / "a.xlsx"
        write_xlsx(path, day_sheet())
        return path.read_bytes()

    def _add(self, screen_id: str, name: str, data: bytes):
        return self.request("POST", "/api/input/add", None, screen_id=screen_id,
                            body=data, headers={"X-Filename": name})

    def test_受け取って数える(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        status, data = self._add(screen_id, "a.xlsx", self._book())
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)

    def test_xlsx以外は受けない(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        _status, data = self._add(screen_id, "memo.txt", b"x")
        self.assertFalse(data["ok"])
        self.assertIn(".xlsx", data["message"])

    def test_上限を超えたら受けない(self) -> None:
        limit = app_config.load()["limits"]["max_files"]
        app_config.load()["limits"]["max_files"] = 2
        self.addCleanup(lambda: app_config.load()["limits"]
                        .__setitem__("max_files", limit))
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        book = self._book()
        for _ in range(2):
            self._add(screen_id, "a.xlsx", book)
        _status, data = self._add(screen_id, "a.xlsx", book)
        self.assertFalse(data["ok"])
        self.assertIn("2 本まで", data["message"])

    def test_同じ名前が2つ来ても上書きしない(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        book = self._book()
        for _ in range(2):
            self._add(screen_id, "同じ.xlsx", book)
        self.assertEqual(intake.INTAKE.count(), 2)

    def test_数えるだけ(self) -> None:
        screen_id = self.claim()
        status, data = self.request("POST", "/api/input/scan",
                                    {"folder": str(self.books(3))},
                                    screen_id=screen_id)
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 3)

    def test_数えて多すぎれば先に言う(self) -> None:
        """押してから断られるより、押す前に分かるほうがよい。"""
        app_config.load()["limits"]["max_files"] = 1
        self.addCleanup(lambda: app_config.load()["limits"]
                        .__setitem__("max_files", 100))
        screen_id = self.claim()
        _status, data = self.request("POST", "/api/input/scan",
                                     {"folder": str(self.books(2))},
                                     screen_id=screen_id)
        self.assertTrue(data["over"])
        self.assertIn("1 本までです", data["message"])

    def test_集計タブはCSVも受ける(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {"kind": "tally"},
                     screen_id=screen_id)
        status, data = self.request(
            "POST", "/api/input/add", None, screen_id=screen_id,
            body="日付,ロット№\n".encode("utf-8"),
            headers={"X-Filename": "集計.csv", "X-Intake": "tally"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["kind"], "tally")

    def test_変換タブはCSVを受けない(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        _status, data = self._add(screen_id, "集計.csv", b"a,b\n")
        self.assertFalse(data["ok"])
        self.assertIn(".csv", data["message"])

    def test_置き場所はタブごとに分かれる(self) -> None:
        """**片方を片づけても、もう片方は残ります。**"""
        screen_id = self.claim()
        book = self._book()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        self._add(screen_id, "a.xlsx", book)
        self.request("POST", "/api/input/begin", {"kind": "tally"},
                     screen_id=screen_id)
        self.request("POST", "/api/input/add", None, screen_id=screen_id,
                     body=book, headers={"X-Filename": "b.xlsx",
                                         "X-Intake": "tally"})
        self.assertEqual(intake.INTAKE.count(), 1)
        self.assertEqual(intake.TALLY_INTAKE.count(), 1)

        self.request("POST", "/api/input/clear", {"kind": "tally"},
                     screen_id=screen_id)
        self.assertEqual(intake.INTAKE.count(), 1)      # 変換ぶんは残る
        self.assertEqual(intake.TALLY_INTAKE.count(), 0)

    def test_知らない置き場所は断る(self) -> None:
        screen_id = self.claim()
        status, data = self.request("GET", "/api/input?kind=よそ",
                                    screen_id=screen_id)
        self.assertEqual(status, 400)
        self.assertIn("知らない受け取り先", data["reason"])

    def test_片づけられる(self) -> None:
        screen_id = self.claim()
        self.request("POST", "/api/input/begin", {}, screen_id=screen_id)
        self._add(screen_id, "a.xlsx", self._book())
        self.request("POST", "/api/input/clear", {}, screen_id=screen_id)
        self.assertEqual(intake.INTAKE.count(), 0)
