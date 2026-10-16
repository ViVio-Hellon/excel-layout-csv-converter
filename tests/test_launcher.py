"""起動の入口 ── 実行環境の確認と多重起動の判定

**応答で確かめる**ことが要点です(目印のファイルだけでは、強制終了の
あとに「起動しているのに起動していない」が残ります)。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

import launch_guard                                # noqa: E402
import process_manager                             # noqa: E402
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


class StopTests(WebCase):
    """`stop.bat` の中身(`process_manager`)。

    **止まったと確かめてから言います。** 「終わってもらいました」と出た
    のに Python が残っていた、という報告がありました ── 残っていると
    そのフォルダを消せないので、確かめずに成功と言ってはいけません。
    """

    def test_起動していなければ止めるものはない(self) -> None:
        self.assertEqual(process_manager.stop(), 0)

    def test_答えない相手を成功と言わない(self) -> None:
        """ポートは受け付けるのに health が答えない(起動の途中で固まった)。

        前は「何のアプリか答えません ── 触りません」と言って**0を返し**、
        目印まで消していました。`stop.bat` は成功と見なし、Python は
        残ったままでした。
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((app_config.host(), app_config.port()))
        listener.listen(5)                         # 受け付けるが答えない
        self.addCleanup(listener.close)
        launch_guard.write_lock(launch_guard.new_lock(port=app_config.port(),
                                                      token="t", stop_key="s"))
        self.addCleanup(launch_guard.remove_lock)

        code = process_manager.stop()
        self.assertNotEqual(code, 0)               # stop.bat が次の手を出せる
        # **PIDの控えを捨てません** ── 捨てると次に打っても手がかりが無い
        self.assertIsNotNone(launch_guard.read_lock())

    def test_撃ったあと残っていれば残っていると言う(self) -> None:
        launch_guard.write_lock(launch_guard.new_lock(port=app_config.port(),
                                                      token="t", stop_key="s"))
        self.addCleanup(launch_guard.remove_lock)
        with mock.patch.object(process_manager, "_looks_like_our_process",
                               return_value=True), \
             mock.patch.object(process_manager.subprocess, "run"), \
             mock.patch.object(process_manager, "_still_running",
                               return_value=True), \
             mock.patch.object(process_manager.time, "sleep"):
            code = process_manager._stop_recorded(12345)
        self.assertEqual(code, 1)
        self.assertIsNotNone(launch_guard.read_lock())

    def test_止まったと確かめたら目印を消す(self) -> None:
        launch_guard.write_lock(launch_guard.new_lock(port=app_config.port(),
                                                      token="t", stop_key="s"))
        with mock.patch.object(process_manager, "_looks_like_our_process",
                               side_effect=[True, False]), \
             mock.patch.object(process_manager.subprocess, "run"), \
             mock.patch.object(process_manager, "_still_running",
                               return_value=False), \
             mock.patch.object(process_manager.time, "sleep"):
            code = process_manager._stop_recorded(12345)
        self.assertEqual(code, 0)
        self.assertIsNone(launch_guard.read_lock())

    def test_控えが無ければ撃たない(self) -> None:
        """**巻き添えを避けます。** PIDが分からなければ何もしません。"""
        self.assertEqual(process_manager._stop_recorded(0), 1)


class EnvironmentTests(unittest.TestCase):
    def test_実行環境がそろっている(self) -> None:
        self.assertEqual(start_app.check_environment(), [])

    def test_終了コードの意味が重ならない(self) -> None:
        codes = {start_app.EXIT_OK, start_app.EXIT_ENV,
                 start_app.EXIT_ALREADY, start_app.EXIT_PORT}
        self.assertEqual(len(codes), 4)
