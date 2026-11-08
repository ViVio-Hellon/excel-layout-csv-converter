"""起動の入口 ── 実行環境の確認と多重起動の判定

**応答で確かめる**ことが要点です(目印のファイルだけでは、強制終了の
あとに「起動しているのに起動していない」が残ります)。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
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

    def test_番号が使い回されていたら撃たずに片付ける(self) -> None:
        """**ここが報告の肝です。**

        ほかのツールで「再起動のあと、まったく別のプログラムが同じ番号を
        持ち、いつまでも起動中と判断する」不具合がありました。こちらは
        応答で判定するので「起動中」にはなりませんが、**残った目印のPIDを
        撃つとき**は番号の使い回しが怖い ── 起動時刻も見て、合わなければ
        撃たずに目印を片付けます。
        """
        info = launch_guard.new_lock(port=app_config.port(), token="t",
                                     stop_key="s")
        # 目印は「35分前に始まったプロセス」と言っているが、そのPIDは
        # いま動いている別もの(= 番号が使い回された)
        info.proc_started_at = info.proc_started_at - 2100
        launch_guard.write_lock(info)
        with mock.patch.object(process_manager.subprocess, "run") as killed:
            code = process_manager._stop_recorded(info.pid,
                                                  launch_guard.read_lock())
        self.assertEqual(code, 1)
        killed.assert_not_called()                 # **撃っていない**
        self.assertIsNone(launch_guard.read_lock())  # 目印は片付いた


class StaleLockTests(WebCase):
    """**残った目印の片付け**(ほかのツールの報告を受けて ── 1.11.3)。

    報告は「再起動のあとPIDが使い回され、いつまでも起動中と判断する」。
    こちらは応答で判定するので「起動中」には化けませんが、**ポートは
    握られているのに答えない**ときに、控えのPIDを撃つ道があります ──
    そこで番号と**起動時刻**の両方を見ます。
    """

    def _unknown(self):
        return launch_guard.GuardResult(
            state=launch_guard.UNKNOWN, lock=launch_guard.read_lock(),
            detail="ポートは使われていますが、何のアプリか答えません")

    def test_番号の使い回しなら撃たずに目印だけ片付ける(self) -> None:
        info = launch_guard.new_lock(port=app_config.port(), token="t")
        info.proc_started_at = info.proc_started_at - 2100   # 35分 ずれている
        launch_guard.write_lock(info)
        log = logging.getLogger("試験")
        with mock.patch.object(process_manager, "_stop_recorded") as killed:
            went = start_app.clear_stale(self._unknown(), log)
        self.assertFalse(went)                     # 起動は続けない(ポートは他人)
        killed.assert_not_called()                 # **撃っていない**
        self.assertIsNone(launch_guard.read_lock())

    def test_本人が固まっていれば終わらせてから起動する(self) -> None:
        """**5秒待って、答えなければ終わらせます。**"""
        launch_guard.write_lock(
            launch_guard.new_lock(port=app_config.port(), token="t"))
        log = logging.getLogger("試験")
        with mock.patch.object(start_app, "WAIT_FOR_SILENT_SEC", 0.2), \
             mock.patch.object(launch_guard, "probe_health", return_value=None), \
             mock.patch.object(process_manager, "_stop_recorded",
                               return_value=0) as killed:
            went = start_app.clear_stale(self._unknown(), log)
        self.assertTrue(went)
        killed.assert_called_once()

    def test_待っているあいだに答えたら撃たない(self) -> None:
        launch_guard.write_lock(
            launch_guard.new_lock(port=app_config.port(), token="t"))
        log = logging.getLogger("試験")
        with mock.patch.object(launch_guard, "probe_health",
                               return_value={"app_id": app_config.app_id()}), \
             mock.patch.object(process_manager, "_stop_recorded") as killed:
            went = start_app.clear_stale(self._unknown(), log)
        self.assertTrue(went)
        killed.assert_not_called()

    def test_起動時刻が読めなければ触らない(self) -> None:
        launch_guard.write_lock(
            launch_guard.new_lock(port=app_config.port(), token="t"))
        log = logging.getLogger("試験")
        with mock.patch.object(launch_guard, "process_started_at",
                               return_value=None), \
             mock.patch.object(process_manager, "_stop_recorded") as killed:
            went = start_app.clear_stale(self._unknown(), log)
        self.assertFalse(went)
        killed.assert_not_called()
        self.assertIsNotNone(launch_guard.read_lock())   # 手がかりは残す


class JudgeRecordedTests(unittest.TestCase):
    """番号と起動時刻で「本人か」を見る(`launch_guard.judge_recorded`)。"""

    def test_自分のことは本人と分かる(self) -> None:
        info = launch_guard.new_lock(port=8741, token="t")
        self.assertGreater(info.proc_started_at, 0)      # OSから読めている
        verdict, _why = launch_guard.judge_recorded(info)
        self.assertEqual(verdict, launch_guard.SAME)

    def test_起動時刻がちがえば別もの(self) -> None:
        info = launch_guard.new_lock(port=8741, token="t")
        info.proc_started_at -= 2100
        verdict, why = launch_guard.judge_recorded(info)
        self.assertEqual(verdict, launch_guard.OTHER)
        self.assertIn("使い回され", why)

    def test_居ないPIDは別もの(self) -> None:
        info = launch_guard.new_lock(port=8741, token="t")
        info.pid = 999_999
        verdict, _why = launch_guard.judge_recorded(info)
        self.assertIn(verdict, (launch_guard.OTHER, launch_guard.UNSURE))

    def test_前の版の目印は書いた時刻で見る(self) -> None:
        """起動時刻が入っていない目印 ──
        **書いたあとに始まったプロセスは、書いた本人ではありません。**"""
        info = launch_guard.new_lock(port=8741, token="t")
        info.proc_started_at = 0.0                 # 前の版が書いたもの
        launch_guard.write_lock(info)
        self.addCleanup(launch_guard.remove_lock)
        # 目印の時刻を「このプロセスより前」に見せる → 本人とみなす
        os.utime(launch_guard.lock_path(), (time.time(), time.time()))
        self.assertEqual(launch_guard.judge_recorded(info)[0],
                         launch_guard.SAME)
        # 目印が**このプロセスより古い**なら、別もの
        old = launch_guard.process_started_at(os.getpid()) - 60
        os.utime(launch_guard.lock_path(), (old, old))
        verdict, why = launch_guard.judge_recorded(info)
        self.assertEqual(verdict, launch_guard.OTHER)
        self.assertIn("目印を書いたあと", why)


class EnvironmentTests(unittest.TestCase):
    def test_実行環境がそろっている(self) -> None:
        self.assertEqual(start_app.check_environment(), [])

    def test_終了コードの意味が重ならない(self) -> None:
        codes = {start_app.EXIT_OK, start_app.EXIT_ENV,
                 start_app.EXIT_ALREADY, start_app.EXIT_PORT,
                 start_app.EXIT_OTHER_MODE}
        self.assertEqual(len(codes), 5)
