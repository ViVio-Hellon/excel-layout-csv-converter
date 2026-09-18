"""起動の入口 ── 実行環境の確認と多重起動の判定

**応答で確かめる**ことが要点です(目印のファイルだけでは、強制終了の
あとに「起動しているのに起動していない」が残ります)。
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

import launch_guard                                # noqa: E402
import start_app                                   # noqa: E402
from converter import app_config                   # noqa: E402
from tests._web import WebCase                     # noqa: E402


class _Other(BaseHTTPRequestHandler):
    """同じポートに居る**別のアプリ**のふり。"""

    def do_GET(self):                              # noqa: N802
        body = json.dumps({"app_id": "よそのアプリ",
                           "display_name": "別のツール"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                  # noqa: A003
        pass


class GuardTests(WebCase):
    def test_空いていれば起動してよい(self) -> None:
        self.assertEqual(launch_guard.check_existing().state, launch_guard.FREE)

    def test_別のアプリが使っていたら見分ける(self) -> None:
        """**HTTPが返ったことと、うちが起動していることは別。**"""
        httpd = ThreadingHTTPServer((app_config.host(), app_config.port()), _Other)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        found = launch_guard.check_existing()
        self.assertEqual(found.state, launch_guard.FOREIGN)
        self.assertIn("別のアプリ", launch_guard.refusal_text(found))
        self.assertIn("server.port", launch_guard.refusal_text(found))

    def test_目印を書いて読んで消せる(self) -> None:
        info = launch_guard.new_lock(port=8741, token="ひみつ")
        launch_guard.write_lock(info)
        again = launch_guard.read_lock()
        self.assertEqual(again.token, "ひみつ")
        self.assertEqual(again.app_id, app_config.app_id())
        launch_guard.remove_lock()
        self.assertIsNone(launch_guard.read_lock())

    def test_合言葉は目印の中だけにある(self) -> None:
        """ブラウザで開いた別のサイトからは読めない場所に置く(2.8)。"""
        launch_guard.write_lock(launch_guard.new_lock(port=8741, token="ひみつ"))
        self.addCleanup(launch_guard.remove_lock)
        text = launch_guard.lock_path().read_text(encoding="utf-8")
        self.assertIn("ひみつ", text)
        self.assertTrue(str(launch_guard.lock_path()).startswith(
            str(app_config.local_root())))

    def test_繋がらなければ残った目印を消す(self) -> None:
        launch_guard.write_lock(launch_guard.new_lock(port=8741, token="古い"))
        self.assertEqual(launch_guard.check_existing().state, launch_guard.FREE)
        self.assertIsNone(launch_guard.read_lock())


class EnvironmentTests(unittest.TestCase):
    def test_実行環境がそろっている(self) -> None:
        self.assertEqual(start_app.check_environment(), [])

    def test_終了コードの意味が重ならない(self) -> None:
        codes = {start_app.EXIT_OK, start_app.EXIT_ENV,
                 start_app.EXIT_ALREADY, start_app.EXIT_PORT}
        self.assertEqual(len(codes), 4)
