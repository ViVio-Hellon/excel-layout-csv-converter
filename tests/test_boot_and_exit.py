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
from converter import (app_config, awake_clock, boot_screen,   # noqa: E402
                       idle_exit)
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


class _Hand:
    """手で進める時計。**スリープも「進まない」も、こちらで作れます。**"""

    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def pass_(self, seconds: float) -> None:
        self.t += seconds


class IdleExitTests(unittest.TestCase):
    """**間違って落とさない**ための決まり。

    猶予・処理中・未接続のほかに、**スリープ復帰**と**裏に回ったタブ**を
    見ます ── ほかのツールで「スリープ復帰時の時計のずれやブラウザの
    タブ休止により問い合わせが止まり、サーバーが誤って終了してしまう」
    という報告があり、このアプリも同じ作りでした(1.11.0)。
    """

    def _exit(self, **kwargs):
        self.stopped = []
        self.hand = _Hand()
        return idle_exit.IdleExit(busy=kwargs.pop("busy", lambda: False),
                                  stop=self.stopped.append,
                                  clock=self.hand.now, **kwargs)

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
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertIn("閉じられました", watcher._reason())

    def test_心拍が戻れば取り消す(self) -> None:
        watcher = self._exit(minutes=0)
        watcher.mark_alive()
        watcher.mark_closed()
        watcher.mark_alive()                        # 再読込で戻ってきた
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertEqual(watcher._reason(), "")

    def test_処理中は落とさない(self) -> None:
        watcher = self._exit(minutes=0, busy=lambda: True)
        watcher.mark_alive()
        watcher.mark_closed()
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertEqual(watcher._reason(), "")

    def test_心拍が途切れれば終わる(self) -> None:
        """**見えているタブ**が黙ったときは、これまでどおり終わります。"""
        watcher = self._exit(minutes=1)
        watcher.mark_alive()
        self.hand.pass_(120)
        self.assertIn("分ありませんでした", watcher._reason())

    # -- スリープ復帰 ------------------------------------------------
    def test_スリープのあいだは無通信に数えない(self) -> None:
        """**フタを開けた瞬間に落としません。**

        見るのは「起きていた時間」です(`converter/awake_clock.py`)。
        Windows はスリープ中も時計が進むので、壁の時計で数えると
        35分眠ったあと、ブラウザが心拍を送る前に終わっていました。
        """
        clock = awake_clock.AwakeClock(mono=self.mono, wall=self.wall)
        clock.start(thread=False)                   # 見張りは手で回す
        watcher = idle_exit.IdleExit(busy=lambda: False,
                                     stop=[].append, minutes=30,
                                     clock=clock.now)
        watcher.mark_alive()
        self.jump(35 * 60)                          # フタを閉じて35分
        found = clock.check()                       # 起きて最初の見張り
        self.assertIsNotNone(found)
        self.assertTrue(found.counted)
        self.assertEqual(watcher._reason(), "")     # **落とさない**
        # 眠っていた分を差し引いても、**起きたあとの無通信は数えます**
        self.awake(31 * 60, clock)
        self.assertIn("分ありませんでした", watcher._reason())

    # -- 裏に回ったタブ ----------------------------------------------
    def test_裏に回ったタブは心拍が止まっても落とさない(self) -> None:
        """ブラウザは見えていないタブのタイマーを**止めます**。"""
        watcher = self._exit(minutes=30)
        watcher.mark_hidden("タブ1", page="p1")
        self.hand.pass_(35 * 60)                    # 凍結されて何も来ない
        self.assertEqual(watcher._reason(), "")
        self.assertEqual(watcher.status()["hidden"], 1)

    def test_裏のまま置き去りならいつかは終わる(self) -> None:
        """**閉じ忘れを永久に抱えません**(既定7日)。"""
        watcher = self._exit(minutes=30, hidden_sec=60.0)
        watcher.mark_hidden("タブ1")
        self.hand.pass_(61)
        self.assertIn("裏に回ったまま", watcher._reason())

    def test_表に戻れば元の見切りに戻る(self) -> None:
        watcher = self._exit(minutes=30)
        watcher.mark_hidden("タブ1")
        self.hand.pass_(10 * 60)
        watcher.mark_alive("タブ1")                 # 戻ってきた
        self.assertEqual(watcher.status()["hidden"], 0)
        self.hand.pass_(31 * 60)
        self.assertIn("分ありませんでした", watcher._reason())

    # -- 閉じたページ番号 --------------------------------------------
    def test_閉じたページからの遅れた合図は受けない(self) -> None:
        """閉じると「裏に回った」と「閉じた」が続けて飛び、着く順は不定。"""
        watcher = self._exit(minutes=30)
        watcher.mark_alive("タブ1", page="p1")
        watcher.mark_closed("タブ1", page="p1")
        self.assertFalse(watcher.mark_hidden("タブ1", page="p1"))
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertIn("閉じられました", watcher._reason())

    def test_再読込した新しいページは落とされない(self) -> None:
        """`pagehide` は再読込でも凍結でも飛びます。**番号で見分けます。**"""
        watcher = self._exit(minutes=30)
        watcher.mark_alive("タブ1", page="p1")
        watcher.mark_alive("タブ1", page="p2")      # 新しいページが先に着いた
        watcher.mark_closed("タブ1", page="p1")     # 古いページの「閉じた」
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertEqual(watcher._reason(), "")     # 新しい画面は生きている

    def test_2枚のうち1枚を閉じても終わらない(self) -> None:
        watcher = self._exit(minutes=30)
        watcher.mark_alive("タブ1", page="p1")
        watcher.mark_alive("タブ2", page="p2")
        watcher.mark_closed("タブ2", page="p2")
        self.hand.pass_(idle_exit.GRACE_SEC + 1)
        self.assertEqual(watcher._reason(), "")
        self.assertEqual(watcher.status()["screens"], 1)

    # -- 時計の作り(試験用の道具)------------------------------------
    def setUp(self) -> None:
        self._mono = 500.0
        self._wall = 1_700_000_000.0

    def mono(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall

    def jump(self, seconds: float) -> None:
        """**眠っていた**ぶん進める(Windows型 ── 壁も monotonic も進む)。"""
        self._wall += seconds
        self._mono += seconds

    def awake(self, seconds: float, clock) -> None:
        """**起きていた**ぶん進める。見張りが刻んでいる状態を作ります。

        空きで眠りを見分ける作りなので、一気に進めると眠っていたことに
        なります ── 起きている時間は、見張りの間隔ごとに刻みます。
        """
        step = clock.tick_sec
        for _ in range(int(seconds / step)):
            self._wall += step
            self._mono += step
            clock.check()


class AwakeClockTests(unittest.TestCase):
    """起きていた時間だけを数える時計(`converter/awake_clock.py`)。"""

    def setUp(self) -> None:
        self.mono = 100.0
        self.wall = 1_700_000_000.0
        self.clock = awake_clock.AwakeClock(mono=lambda: self.mono,
                                            wall=lambda: self.wall)

    def pass_(self, seconds: float, *, mono: bool = True,
              wall: bool = True) -> None:
        if mono:
            self.mono += seconds
        if wall:
            self.wall += seconds

    def test_見張っていなければただのmonotonic(self) -> None:
        """立てていなければ、空きが眠っていた証拠になりません。"""
        before = self.clock.now()
        self.pass_(600)
        self.assertAlmostEqual(self.clock.now() - before, 600)
        self.assertEqual(self.clock.slept, 0.0)

    def test_眠っていた分を差し引く(self) -> None:
        """Windows型(monotonic も進む)。**差し引いて数えません。**"""
        self.clock.start(thread=False)
        before = self.clock.now()
        self.pass_(1800)                            # 30分 眠った
        found = self.clock.check()
        self.assertIsNotNone(found)
        self.assertTrue(found.counted)
        self.assertAlmostEqual(found.seconds, 1800 - awake_clock.TICK_SEC)
        # 起きていた時間は、見張り1回ぶんしか進んでいません
        self.assertAlmostEqual(self.clock.now() - before,
                               awake_clock.TICK_SEC)

    def test_壁の時計だけ飛んだとき(self) -> None:
        """Linuxのスリープ・時刻合わせ。monotonic は止まっています。"""
        self.clock.start(thread=False)
        before = self.clock.now()
        self.pass_(1800, mono=False)                # 壁だけ進んだ
        found = self.clock.check()
        self.assertIsNotNone(found)
        self.assertFalse(found.counted)             # 差し引くものは無い
        self.assertAlmostEqual(self.clock.now() - before, 0)

    def test_少しの遅れでは眠っていたと言わない(self) -> None:
        """重い処理で見張りが遅れた程度では差し引きません。"""
        self.clock.start(thread=False)
        self.pass_(awake_clock.JUMP_SEC - 1)
        self.assertIsNone(self.clock.check())
        self.assertEqual(self.clock.slept, 0.0)

    def test_呼ぶほうが先でも同じ答え(self) -> None:
        """起きた瞬間、見張りと要求のどちらが先に走るかは決まりません。"""
        self.clock.start(thread=False)
        before = self.clock.now()
        self.pass_(1800)
        self.assertAlmostEqual(self.clock.now() - before,
                               awake_clock.TICK_SEC)
        self.assertIsNone(self.clock.check())       # もう済んでいる


class IdleIntervalTests(unittest.TestCase):
    def test_心拍の間隔は1か所で決まる(self) -> None:
        """画面にも同じ数を書くと、片方を直したときに自動終了が誤る。"""
        self.assertGreater(idle_exit.HEARTBEAT_MS, 0)
        self.assertLess(idle_exit.HEARTBEAT_MS / 1000 * 3, idle_exit.GRACE_SEC)
