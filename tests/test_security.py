"""外から勝手に操作されないこと(`app/__init__.py` の守り)

`127.0.0.1` にしか待ち受けていませんが、利用者が見ている**別のサイト**が
このURLへ書き込みを投げることはできます(返事は読めませんが、効いて
しまう)。変換を勝手に止められては困るので、4つで守ります。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._web import TOKEN, WebCase              # noqa: E402


class SecurityTests(WebCase):
    def test_合言葉が無ければ通さない(self) -> None:
        status, _data = self.request("GET", "/api/settings", token="")
        self.assertEqual(status, 401)

    def test_合言葉が違えば通さない(self) -> None:
        status, _data = self.request("GET", "/api/settings", token="にせもの")
        self.assertEqual(status, 401)

    def test_起動確認は合言葉なしで答える(self) -> None:
        """**まだ合言葉を知らない相手**が正当に問い合わせます(2.3 / 2.4)。"""
        status, data = self.request("GET", "/api/health", token="")
        self.assertEqual(status, 200)
        self.assertTrue(data["app_id"])

    def test_心拍は合言葉なしで通る(self) -> None:
        """要求すると、切れた画面が黙って死んだ扱いになり自動終了が誤る。"""
        status, _data = self.request("POST", "/api/alive", {}, token="")
        self.assertEqual(status, 200)

    def test_別のドメインからは断る(self) -> None:
        """外のドメインを 127.0.0.1 に向けられても、Hostが合いません。"""
        status, _data = self.request("GET", "/api/settings",
                                     headers={"Host": "evil.example"})
        self.assertEqual(status, 400)

    def test_別のページからの書き込みは断る(self) -> None:
        screen_id = self.claim()
        status, _data = self.request("POST", "/api/job/cancel", {},
                                     screen_id=screen_id,
                                     headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_同じ画面からの書き込みは通る(self) -> None:
        screen_id = self.claim()
        status, _data = self.request("POST", "/api/job/cancel", {},
                                     screen_id=screen_id,
                                     headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)

    def test_静的ファイルは外へ出られない(self) -> None:
        status, _data = self.request("GET", "/static/../../config/app.json")
        self.assertEqual(status, 404)

    def test_画面のHTMLは合言葉を要求しない(self) -> None:
        """アドレス欄から開く経路なので。中身は空の器です。"""
        from server import Request
        response = self.app.dispatch(Request(method="GET", path="/",
                                             headers={"host": "127.0.0.1"}))
        self.assertEqual(response.status, 200)

    def test_画面は合言葉を受け取る(self) -> None:
        """準備が終わればアプリ画面。**合言葉はそこに埋め込まれます。**"""
        from server import Request
        self.app.mark_ready()
        response = self.app.dispatch(Request(method="GET", path="/",
                                             headers={"host": "127.0.0.1"}))
        self.assertIn(TOKEN, response.body.decode("utf-8"))
