"""画面は1枚だけ ── **2枚目は開かせない**

判定は3段(プロセス・サーバ・ブラウザ)。ここで見るのは真ん中のサーバ側
です。**再読込を断らない**ことも同じだけ大事なので、両方を見ます。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

from converter import screen_lock                  # noqa: E402
from tests._web import WebCase                     # noqa: E402


class LockTests(unittest.TestCase):
    """占有そのもの(画面もサーバも通さずに見る)。"""

    def setUp(self) -> None:
        self.lock = screen_lock.ScreenLock()

    def test_最初の1枚は通る(self) -> None:
        self.assertTrue(self.lock.claim("tab-A").ok)

    def test_2枚目は断る(self) -> None:
        self.lock.claim("tab-A")
        found = self.lock.claim("tab-B")
        self.assertFalse(found.ok)
        self.assertIn("すでに別の画面", found.detail)

    def test_識別子が無ければ断る(self) -> None:
        self.assertFalse(self.lock.claim("").ok)

    def test_同じタブの再読込は断らない(self) -> None:
        """**再読込で締め出さない。** 開き直せない画面を作らないため。"""
        first = self.lock.claim("tab-A").screen_id
        second = self.lock.claim("tab-A").screen_id
        self.assertNotEqual(first, second)
        self.assertFalse(self.lock.holds(first))
        self.assertTrue(self.lock.holds(second))

    def test_応答が途切れれば空く(self) -> None:
        lock = screen_lock.ScreenLock(timeout_seconds=0)
        lock.claim("tab-A")
        time.sleep(0.01)
        self.assertTrue(lock.claim("tab-B").ok)

    def test_心拍が占有を延ばす(self) -> None:
        """**生存確認は占有の延長を兼ねます。** ここが抜けると自分が
        締め出されます(実際に踏みました)。"""
        lock = screen_lock.ScreenLock(timeout_seconds=1)
        screen_id = lock.claim("tab-A").screen_id
        for _ in range(3):
            time.sleep(0.4)
            self.assertTrue(lock.beat(screen_id))
        self.assertTrue(lock.holds(screen_id))

    def test_持ち主でなければ心拍は通らない(self) -> None:
        self.lock.claim("tab-A")
        self.assertFalse(self.lock.beat("よその番号"))


class ScreenRouteTests(WebCase):
    """画面からの受け取りと、断られた画面の扱い。"""

    def test_2枚目は断られる(self) -> None:
        self.claim("tab-A")
        status, data = self.request("POST", "/api/screen/claim", {"tab_id": "tab-B"})
        self.assertEqual(status, 409)
        self.assertIn("2枚開くと", data["detail"])

    def test_閉じれば空く(self) -> None:
        screen_id = self.claim("tab-A")
        self.request("POST", "/api/screen/release", {}, screen_id=screen_id)
        self.assertEqual(
            self.request("POST", "/api/screen/claim", {"tab_id": "tab-B"})[0], 200)

    def test_占有していない画面の操作は効かない(self) -> None:
        self.claim("tab-A")
        for method, path in (("GET", "/api/job"), ("POST", "/api/job/start"),
                             ("POST", "/api/job/cancel"), ("GET", "/api/settings"),
                             ("POST", "/api/input/clear")):
            status, data = self.request(method, path,
                                        {} if method == "POST" else None,
                                        screen_id="よその番号")
            self.assertEqual(status, 409, path)
            self.assertTrue(data.get("screen_lost"), path)

    def test_心拍は占有を教える(self) -> None:
        screen_id = self.claim("tab-A")
        self.assertTrue(self.request("POST", "/api/alive", {},
                                     screen_id=screen_id)[1]["screen_ok"])
        self.assertFalse(self.request("POST", "/api/alive", {},
                                      screen_id="よその番号")[1]["screen_ok"])

    def test_心拍は業務データを返さない(self) -> None:
        """トークン不要の口なので、**状態だけ**にします。"""
        _status, data = self.request("POST", "/api/alive", {}, token="")
        self.assertEqual(set(data) - {"ok", "screen_ok", "ready"}, set())
