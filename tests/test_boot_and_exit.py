"""起動待機画面と、終わり方

- 待機画面は**アプリ本体を読む前**に出る(`boot_server`)
- 引き継ぎで**待ち受けを開き直さない**(`Handover`)
- 実行中の停止は確かめてから(基盤仕様書 2.8)
- 画面が居なくなったら終わる(`converter/idle_exit.py`)
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

import boot_server                                 # noqa: E402
from converter import app_config, boot_screen, idle_exit   # noqa: E402
from server import Request                         # noqa: E402
from tests._web import STOP_KEY, WebCase           # noqa: E402


class BootScreenTests(unittest.TestCase):
    def test_外部への要求を持たない(self) -> None:
        """1往復で出す。**外へ出られない端末でも出ます。**"""
        html = boot_screen.render(display_name="試験", version_label="VER1.0.0",
                                  app_id="x")
        for word in ("http://", "https://", "<img", "<link"):
            self.assertNotIn(word, html)

    def test_色はtokens_cssから採る(self) -> None:
        """色の出どころを2つにしない(直したほうと直していないほうが出る)。"""
        found = boot_screen.colors()
        self.assertTrue(found["--boot-bg"].startswith("#"))
        tokens = (app_config.APP_ROOT / "app" / "static" / "css" / "tokens.css")
        self.assertIn(found["--boot-bg"], tokens.read_text(encoding="utf-8"))

    def test_段は細かくしすぎない(self) -> None:
        self.assertEqual(len(boot_screen.STAGES), 4)


class BootServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boot = boot_server.BootApp(app_config.port(), stop_key=STOP_KEY)
        self.handover = boot_server.Handover(self.boot)

    def _get(self, path: str):
        return self.handover.dispatch(Request(method="GET", path=path))

    def _stop(self, *, key: str = STOP_KEY):
        import json
        body = json.dumps({"stop_key": key}).encode("utf-8")
        return self.handover.dispatch(Request(method="POST",
                                              path="/api/shutdown", body=body))

    def test_待機画面を出す(self) -> None:
        response = self._get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.content_type)

    def test_健康確認は本体と同じ形(self) -> None:
        """形が違うと、待機画面と launch_guard の両方が場合分けを持つ。"""
        import json
        found = json.loads(self._get("/api/health").body.decode("utf-8"))
        for key in ("app_id", "display_name", "version", "ready", "stage",
                    "stage_key", "startup_error", "pid", "port"):
            self.assertIn(key, found)
        self.assertFalse(found["ready"])

    def test_業務の要求にはまだと答える(self) -> None:
        """404 にすると「無い」に見えます。**まだであることを言う。**"""
        self.assertEqual(self._get("/api/job").status, 503)

    def test_待機画面でも停止の合図で止まる(self) -> None:
        """**本体を組み立てられなかったときに、ここが効きます。**

        失敗すると待機画面に理由を出したまま残ります(読めるように)。
        ここが止める合図に答えないと `stop.bat` でも `--force` でも
        止まらず、Python が残って**フォルダを消せなくなります** ──
        実際にそうなった報告があり、答えるようにしました。
        """
        stopped = []
        self.boot.shutdown_hook = stopped.append
        self.boot.mark_error("必要な部品がありません")
        response = self._stop()
        self.assertEqual(response.status, 200)
        self.assertEqual(len(stopped), 1)
        self.assertIn("待機画面", stopped[0])

    def test_にせの合図では止まらない(self) -> None:
        """合図は `runtime/instance.json` の中だけにあります。"""
        stopped = []
        self.boot.shutdown_hook = stopped.append
        self.assertEqual(self._stop(key="にせもの").status, 403)
        self.assertFalse(stopped)

    def test_合図を持っていないうちは止められない(self) -> None:
        """確かめようがないときは断ります(PIDの道が残っています)。"""
        boot = boot_server.BootApp(app_config.port())      # 合図なし
        stopped = []
        boot.shutdown_hook = stopped.append
        import json
        body = json.dumps({"stop_key": ""}).encode("utf-8")
        response = boot.dispatch(Request(method="POST", path="/api/shutdown",
                                         body=body))
        self.assertEqual(response.status, 403)
        self.assertFalse(stopped)

    def test_引き継いだら本体が答える(self) -> None:
        class Fake:
            def dispatch(self, request):
                from server import json_response
                return json_response({"ok": True, "from": "本体"})

        self.handover.install(Fake())
        self.assertTrue(self.handover.handed_over)
        self.assertIn("本体", self._get("/").body.decode("utf-8"))


class FailedBootTests(WebCase):
    """**本体を組み立てられなかったときに、止められるか。**

    現場の報告: 起動の途中で失敗したあと `stop.bat` でも `--force` でも
    止まらず、Python が残って**フォルダを消せなくなった**(タスク
    マネージャーで切るしかなかった)。ここが通れば、同じ状況でも
    `stop.bat` で片づきます。
    """

    def test_起動に失敗しても止められる(self) -> None:
        import launch_guard
        import start_app

        def boom(log) -> None:
            raise RuntimeError("必要な部品がありません(わざと)")

        codes: list[int] = []
        with mock.patch.object(start_app, "warm_up", boom), \
             mock.patch.object(start_app, "tell", lambda *a, **k: False):
            runner = threading.Thread(
                target=lambda: codes.append(
                    start_app.start(open_browser_flag=False)),
                daemon=True)
            runner.start()
            try:
                lock = self._wait_for_lock()
                # **理由を出したまま待っています**(読めるように)
                found = launch_guard.check_existing()
                self.assertEqual(found.state, launch_guard.OURS)
                self.assertFalse(found.health.get("ready"))
                self.assertIn("部品", found.health.get("startup_error", ""))

                # stop.bat と同じ道 ── 合図は目印の中だけにあります
                code, answer = launch_guard.request_shutdown(
                    lock.port, lock.token, stop_key=lock.stop_key)
                self.assertEqual(code, 200, answer)
                runner.join(timeout=10)
            finally:
                if runner.is_alive():
                    self.fail("止まりませんでした(待機画面が合図に答えていない)")
        self.assertFalse(runner.is_alive())
        self.assertEqual(codes, [start_app.EXIT_ENV])
        self.assertIsNone(launch_guard.read_lock())   # 片づけてから終わる

    def _wait_for_lock(self):
        import launch_guard

        for _ in range(100):
            lock = launch_guard.read_lock()
            if lock and lock.port:
                return lock
            time.sleep(0.05)
        self.fail("待ち受けが立ちませんでした")


class ShutdownTests(WebCase):
    """止めてよいのは**いま使っている画面**と **stop.bat** だけ。"""

    def test_合言葉が無ければ通らない(self) -> None:
        stopped = []
        self.app.shutdown_hook = stopped.append
        status, _data = self.request("POST", "/api/shutdown", {}, token="にせもの")
        self.assertEqual(status, 401)
        self.assertFalse(stopped)

    def test_起動トークンだけでは止められない(self) -> None:
        """**断られた2枚目のタブも起動トークンは持っています**(画面のHTML)。

        それで止められると、ちゃんと開いている画面の作業を、居ないはずの
        タブが落とせてしまいます。
        """
        stopped = []
        self.app.shutdown_hook = stopped.append
        self.claim("tab-A")                        # 画面は1枚目が持っている
        status, data = self.request("POST", "/api/shutdown", {})
        self.assertEqual(status, 403)
        self.assertIn("出どころ", data["detail"])
        self.assertFalse(stopped)

    def test_いま使っている画面なら止まる(self) -> None:
        stopped = []
        self.app.shutdown_hook = stopped.append
        screen_id = self.claim("tab-A")
        status, _data = self.request("POST", "/api/shutdown", {},
                                     screen_id=screen_id)
        self.assertEqual(status, 200)
        self.assertTrue(stopped)

    def test_停止の合図があれば画面が無くても止まる(self) -> None:
        """`stop.bat` は画面を持たず、ローカル領域の合図を持ってきます。"""
        stopped = []
        self.app.shutdown_hook = stopped.append
        self.claim("tab-A")
        status, _data = self.request("POST", "/api/shutdown",
                                     {"stop_key": STOP_KEY})
        self.assertEqual(status, 200)
        self.assertTrue(stopped)

    def test_にせの合図では止まらない(self) -> None:
        stopped = []
        self.app.shutdown_hook = stopped.append
        status, _data = self.request("POST", "/api/shutdown",
                                     {"stop_key": "にせの合図"})
        self.assertEqual(status, 403)
        self.assertFalse(stopped)

    def test_変換中は確かめてから(self) -> None:
        from converter import jobs

        screen_id = self.claim()
        self.settings(screen_id, input_dir=str(self.books(2)),
                      output_dir=str(self.tmp / "out"), line="LS")
        self.request("POST", "/api/job/start", {"source": "folder"},
                     screen_id=screen_id)
        stopped = []
        self.app.shutdown_hook = stopped.append
        status, data = self.request("POST", "/api/shutdown", {},
                                    screen_id=screen_id)
        self.wait_for_job()
        if status == 409:                          # まだ走っていれば断られる
            self.assertTrue(data["running"])
            self.assertFalse(stopped)


class IdleExitTests(unittest.TestCase):
    """**間違って落とさない**ための3つ(猶予・処理中・未接続)。"""

    def _exit(self, **kwargs):
        self.stopped = []
        return idle_exit.IdleExit(busy=kwargs.pop("busy", lambda: False),
                                  stop=self.stopped.append, **kwargs)

    def test_1度も繋がっていなければ落とさない(self) -> None:
        """`--no-browser` で立てておく使い方を巻き添えにしない。"""
        watcher = self._exit(minutes=0)
        watcher.mark_closed()
        self.assertEqual(watcher._reason(), "")

    def test_閉じた合図のあと猶予を置く(self) -> None:
        watcher = self._exit(minutes=0)
        watcher.mark_alive()
        watcher.mark_closed()
        self.assertEqual(watcher._reason(), "")     # まだ猶予の中
        watcher._closed_at = time.time() - idle_exit.GRACE_SEC - 1
        self.assertIn("閉じられました", watcher._reason())

    def test_心拍が戻れば取り消す(self) -> None:
        watcher = self._exit(minutes=0)
        watcher.mark_alive()
        watcher.mark_closed()
        watcher.mark_alive()                        # 再読込で戻ってきた
        watcher._closed_at = 0.0
        self.assertEqual(watcher._reason(), "")

    def test_処理中は落とさない(self) -> None:
        watcher = self._exit(minutes=0, busy=lambda: True)
        watcher.mark_alive()
        watcher.mark_closed()
        watcher._closed_at = time.time() - idle_exit.GRACE_SEC - 1
        self.assertEqual(watcher._reason(), "")

    def test_心拍が途切れれば終わる(self) -> None:
        watcher = self._exit(minutes=1)
        watcher.mark_alive()
        watcher._last_beat = time.time() - 120
        self.assertIn("分ありませんでした", watcher._reason())

    def test_心拍の間隔は1か所で決まる(self) -> None:
        """画面にも同じ数を書くと、片方を直したときに自動終了が誤る。"""
        self.assertGreater(idle_exit.HEARTBEAT_MS, 0)
        self.assertLess(idle_exit.HEARTBEAT_MS / 1000 * 3, idle_exit.GRACE_SEC)
