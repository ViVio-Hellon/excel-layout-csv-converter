"""ポートを使わない道 ── **ソケットを1つも開かずに通ること**

【なぜこの試験が要るのか】
デスクトップ版(Tauri)は**ポートを使いません**。外枠(Rust)が Python を
子として起動し、標準入出力で要求を渡します(`bridge.py`)。

「ポートを使わない」は言うだけなら簡単で、**どこか1か所でもソケットを
開いていれば嘘**になります。だから**ソケットを開こうとしたら落ちる**
ようにして、画面と操作を順に通します。

実際、分ける前はここで落ちていました ── `app/` が `server.py` を読んで
いたので、`http.server` → `http.client` → `ssl` まで付いてきていました
(`converter/wire.py` に形を移した理由)。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

import bridge                                      # noqa: E402
from converter import app_config, launch_marker, wire   # noqa: E402
from tests._fixture import day_sheet, write_xlsx    # noqa: E402
from tests._web import STOP_KEY, TOKEN, WebCase     # noqa: E402


class NoSockets(RuntimeError):
    """ソケットを開こうとした ── **これが上がったら移行の前提が崩れています。**"""


def _forbidden(*args, **kwargs):
    raise NoSockets("ソケットを開こうとしました")


class 形を運び方から分ける(unittest.TestCase):
    """`Request` / `Response` は**運び方を知りません**(`converter/wire.py`)。"""

    def test_形だけ借りてもHTTPの部品は付いてこない(self) -> None:
        """`app/` を読むだけで `ssl` まで来ていました。**それを切りました。**"""
        import subprocess

        code = ("import sys; sys.path[:0] = [%r, %r]; import app; "
                "print(','.join(m for m in ('http.server','http.client','ssl',"
                "'socketserver') if m in sys.modules))"
                % (str(ROOT), str(ROOT / "vendor")))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, cwd=str(ROOT))
        self.assertEqual(out.stdout.strip(), "", out.stderr[-400:])

    def test_待ち受け側からも同じ名前で借りられる(self) -> None:
        """取り込み元を2通りにすると、直したほうと直していないほうができます。"""
        import server

        for name in ("Request", "Response", "json_response", "html_response",
                     "refuse"):
            self.assertIs(getattr(server, name), getattr(wire, name), name)


class 外枠の宛先(unittest.TestCase):
    """`Host` の確かめは**待ち受けるための守り**。待ち受けない名前も通します。"""

    def test_ブラウザ版とデスクトップ版の名前が入っている(self) -> None:
        allowed = app_config.allowed_hosts()
        for name in ("127.0.0.1", "localhost"):
            self.assertIn(name, allowed)
        for name in app_config.BRIDGE_HOSTS:
            self.assertIn(name, allowed)

    def test_よその名前は通らない(self) -> None:
        self.assertNotIn("example.com", app_config.allowed_hosts())


class ソケット無しで通る(WebCase):
    """**画面と操作を、ソケットを禁止したまま順に通します。**

    `scripts/ポート無しで通るか見る.py` と同じことを試験の中でやります
    (手で確かめた1回ではなく、毎回確かめるために)。
    """

    def setUp(self) -> None:
        super().setUp()
        self.books = self.tmp / "books"
        self.books.mkdir()
        write_xlsx(self.books / "2026.08.23.xlsx", day_sheet())
        self.out = self.tmp / "out"
        self.app.mark_ready()
        self.screen_id = ""
        # **ここから先、ソケットは1つも作れません**(片付けで戻します)
        for name in ("socket", "create_connection"):
            patch = mock.patch.object(socket, name, _forbidden)
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, method: str, path: str, payload=None, *, body=b"",
             headers=None, host: str = "app.localhost"):
        """外枠から来る形で1件叩く(`bridge.py` が組み立てるのと同じもの)。"""
        head = {"content-type": "application/json", "host": host,
                "x-tool-token": TOKEN}
        if self.screen_id:
            head["x-screen-id"] = self.screen_id
        head.update({k.lower(): v for k, v in (headers or {}).items()})
        raw = json.dumps(payload).encode() if payload is not None else body
        path, _sep, query = path.partition("?")
        response = self.app.dispatch(wire.Request(
            method=method, path=path, query=parse_qs(query), body=raw,
            headers=head))
        try:
            return response.status, json.loads(response.body.decode("utf-8"))
        except ValueError:
            return response.status, {"len": len(response.body)}

    def test_画面と操作と変換が通る(self) -> None:
        self.assertEqual(self.call("GET", "/")[0], 200)

        status, data = self.call("POST", "/api/screen/claim",
                                 {"tab_id": "tab-A"})
        self.assertEqual(status, 200)
        self.screen_id = data["screen_id"]

        self.assertEqual(self.call("POST", "/api/alive",
                                   {"tab": "tab-A", "page": "p1",
                                    "state": "visible"})[0], 200)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)
        self.assertEqual(self.call("GET", "/api/settings")[0], 200)
        self.assertEqual(self.call("POST", "/api/settings", {
            "input_dir": str(self.books), "output_dir": str(self.out),
            "line": "HVC", "ref_dir": "", "stop_csv": "", "log_dir": ""})[0],
            200)

        self.assertEqual(self.call("POST", "/api/input/begin",
                                   {"tab": "convert"})[0], 200)
        body = (self.books / "2026.08.23.xlsx").read_bytes()
        status, data = self.call(
            "POST", "/api/input/add?tab=convert", None, body=body,
            headers={"Content-Type": "application/octet-stream",
                     "X-Filename": "2026.08.23.xlsx"})
        self.assertEqual((status, data["name"]), (200, "2026.08.23.xlsx"))
        self.assertEqual(self.call("GET", "/api/input?tab=convert")[0], 200)

        status, data = self.call("POST", "/api/job/start",
                                 {"tab": "convert", "dry_run": True})
        self.assertEqual(status, 200)
        for _ in range(200):
            _status, found = self.call("GET", "/api/job")
            if (found.get("job") or {}).get("state") != "実行中":
                break
            time.sleep(0.02)
        job = found["job"]
        self.assertEqual(job["state"], "完了")
        self.assertEqual(job["counts"]["read_ok"], 1)

        self.assertEqual(self.call("GET", "/api/diagnostics")[0], 200)
        self.assertEqual(self.call("GET", "/static/js/app.js")[0], 200)

    def test_終了を頼める(self) -> None:
        stopped = []
        self.app.shutdown_hook = stopped.append
        status, data = self.call("POST", "/api/shutdown",
                                 {"force": True, "stop_key": STOP_KEY})
        self.assertEqual((status, data["ok"]), (200, True))
        self.assertEqual(len(stopped), 1)

    def test_ソケットの禁止が本当に効いている(self) -> None:
        """**この試験自体が嘘になっていないか。**"""
        with self.assertRaises(NoSockets):
            socket.socket()


# ==================================================================
# やりとりの形(見出し1行 + 本文)
# ==================================================================
class やりとりの形(unittest.TestCase):
    """1件 = 見出し1行(JSON) + 本文。**本文は JSON に埋めません**(数十MB)。"""

    def test_書いて読める(self) -> None:
        import io

        buf = io.BytesIO()
        bridge.FrameWriter(buf).write({"id": 1, "status": 200,
                                       "headers": [["A", "b"]]},
                                      b"\x00\x01" + "ほん文".encode())
        buf.seek(0)
        head, body = bridge.read_frame(buf, max_body=1024)
        self.assertEqual(head["id"], 1)
        self.assertEqual(head["status"], 200)
        self.assertEqual(body, b"\x00\x01" + "ほん文".encode())

    def test_知らせは本文なし(self) -> None:
        import io

        buf = io.BytesIO()
        bridge.FrameWriter(buf).event("started", url="http://app.localhost/")
        self.assertEqual(json.loads(buf.getvalue().decode().strip()),
                         {"event": "started", "url": "http://app.localhost/"})

    def test_相手が閉じたらNone(self) -> None:
        import io

        self.assertIsNone(bridge.read_frame(io.BytesIO(b""), max_body=16))

    def test_大きすぎる本文は読み捨てて続けられる(self) -> None:
        """**1件断るだけで、次の1件から続けられます**(区切りを保つ)。"""
        import io

        big = json.dumps({"id": 1, "len": 100}).encode() + b"\n" + b"x" * 100
        nxt = json.dumps({"id": 2, "len": 2}).encode() + b"\n" + b"ok"
        buf = io.BytesIO(big + nxt)
        with self.assertRaises(bridge.FrameTooLarge):
            bridge.read_frame(buf, max_body=10)
        head, body = bridge.read_frame(buf, max_body=10)
        self.assertEqual((head["id"], body), (2, b"ok"))

    def test_要求と応答を形に直せる(self) -> None:
        request = bridge.to_request(
            {"method": "post", "path": "/api/x", "query": "a=1&a=2",
             "headers": [["X-Filename", "a.xlsx"]]}, b"{}")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.arg("a"), "1")
        self.assertEqual(request.header("x-filename"), "a.xlsx")
        self.assertEqual(request.header("host"), bridge.DEFAULT_HOST)

        status, headers, body = bridge.from_response(
            wire.json_response({"ok": True}, 201, **{"Cache-Control": "no-store"}))
        self.assertEqual(status, 201)
        self.assertIn(["Cache-Control", "no-store"], headers)
        self.assertEqual(json.loads(body.decode()), {"ok": True})


# ==================================================================
# 本当に子として起動して、標準入出力だけで話す
# ==================================================================
class 外枠のかわりに話す(unittest.TestCase):
    """**外枠(Rust)がやることを Python で代わりにやって、通るか見ます。**

    ここが通れば、残っているのは窓(Tauri)だけです。
    """

    def setUp(self) -> None:
        import subprocess
        import tempfile

        self.local = tempfile.mkdtemp()
        self.tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, XLCSV_LOCAL_DIR=self.local,
                   XLCSV_EXE_PID=str(os.getpid()),
                   PYTHONPATH=str(ROOT / "vendor"))
        self.child = subprocess.Popen(
            [sys.executable, "bridge.py"], cwd=str(ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        self.addCleanup(self._finish)
        self.rid = 0
        self.token = ""
        self.screen_id = ""

    def _finish(self) -> None:
        for pipe in (self.child.stdin, self.child.stdout, self.child.stderr):
            try:
                pipe.close()
            except Exception:                     # noqa: BLE001
                pass
        try:
            self.child.wait(timeout=10)
        except Exception:                         # noqa: BLE001
            self.child.kill()

    # -- 話す ------------------------------------------------------
    def send(self, **head) -> None:
        body = head.pop("body", b"")
        head["len"] = len(body)
        self.child.stdin.write(
            json.dumps(head, ensure_ascii=False).encode() + b"\n" + body)
        self.child.stdin.flush()

    def read(self):
        line = self.child.stdout.readline()
        if not line:
            return None, b""
        head = json.loads(line.decode())
        size = int(head.get("len", 0) or 0)
        body = b""
        while len(body) < size:
            chunk = self.child.stdout.read(size - len(body))
            if not chunk:
                break
            body += chunk
        return head, body

    def call(self, method: str, path: str, payload=None, *, body=b"",
             headers=None):
        self.rid += 1
        head = [["Host", "app.localhost"],
                ["Content-Type", "application/json"]]
        if self.token:
            head.append(["X-Tool-Token", self.token])
        if self.screen_id:
            head.append(["X-Screen-Id", self.screen_id])
        head += [[k, v] for k, v in (headers or {}).items()]
        raw = json.dumps(payload).encode() if payload is not None else body
        path, _sep, query = path.partition("?")
        self.send(id=self.rid, method=method, path=path, query=query,
                  headers=head, body=raw)
        while True:
            got, data = self.read()
            if got is None:
                self.fail("相手が閉じました: "
                          + self.child.stderr.read().decode()[-400:])
            if "event" in got:
                self.events.append(got)
                if got["event"] == "fatal":
                    self.fail(f"起動できませんでした: {got}")
                continue
            try:
                return got, json.loads(data.decode())
            except ValueError:
                return got, {"len": len(data), "text": data.decode("utf-8",
                                                                   "replace")}

    events: list = []

    def wait_ready(self) -> dict:
        self.events = []
        for _ in range(300):
            _head, data = self.call("GET", "/api/health")
            if data.get("ready"):
                return data
            time.sleep(0.05)
        self.fail("準備できませんでした")

    def claim(self) -> None:
        """画面が受け取る(ブラウザがやっているのと同じ順)。"""
        _head, page = self.call("GET", "/")
        import re

        found = re.search(r'"token"\s*:\s*"([^"]+)"', page.get("text", ""))
        self.assertIsNotNone(found, "画面からトークンが採れません")
        self.token = found.group(1)
        _head, data = self.call("POST", "/api/screen/claim",
                                {"tab_id": "tab-A"})
        self.screen_id = data["screen_id"]

    # -- 確かめる --------------------------------------------------
    def test_待機画面から業務画面まで通り変換して終われる(self) -> None:
        head, data = self.call("GET", "/api/health")
        self.assertEqual(head["status"], 200)
        self.assertFalse(data["ready"])           # まだ待機画面
        self.assertEqual(self.call("GET", "/")[0]["status"], 200)

        ready = self.wait_ready()
        self.assertEqual(ready["version"], app_config.version())
        self.assertTrue(any(e.get("event") == "started" for e in self.events),
                        self.events)
        self.claim()

        books = self.tmp / "books"
        books.mkdir()
        write_xlsx(books / "2026.08.23.xlsx", day_sheet())
        out = self.tmp / "out"
        self.assertEqual(self.call("POST", "/api/settings", {
            "input_dir": str(books), "output_dir": str(out), "line": "HVC",
            "ref_dir": "", "stop_csv": "", "log_dir": ""})[0]["status"], 200)
        self.call("POST", "/api/input/begin", {"tab": "convert"})
        _head, data = self.call(
            "POST", "/api/input/add?tab=convert", None,
            body=(books / "2026.08.23.xlsx").read_bytes(),
            headers={"Content-Type": "application/octet-stream",
                     "X-Filename": "2026.08.23.xlsx"})
        self.assertEqual(data["name"], "2026.08.23.xlsx")

        self.call("POST", "/api/job/start", {"tab": "convert"})
        for _ in range(400):
            _head, found = self.call("GET", "/api/job")
            job = found.get("job") or {}
            if job.get("state") != "実行中":
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "完了", job.get("error"))
        self.assertEqual(job["counts"]["read_ok"], 1)
        self.assertEqual(job["written"], 4)       # 4本組のCSV
        self.assertEqual(len(list(out.rglob("*.csv"))), 4)

        # **どのポートでも待ち受けていません**
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex((app_config.host(),
                                                  app_config.port())), 0)
        # デスクトップ版の印が置かれている(ブラウザ版はこれを見て断ります)
        marker = Path(self.local) / "runtime" / "desktop.json"
        self.assertTrue(marker.is_file())
        found = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(found["mode"], "desktop")
        self.assertGreater(found["proc_started_at"], 0)

        # 終了を頼むと `quit` が来て、プロセスが終わり、印が片付く
        self.send(id=999, method="POST", path="/api/shutdown", query="",
                  headers=[["Host", "app.localhost"],
                           ["Content-Type", "application/json"],
                           ["X-Tool-Token", self.token],
                           ["X-Screen-Id", self.screen_id]],
                  body=json.dumps({"force": True}).encode())
        for _ in range(20):
            got, _body = self.read()
            if got is None or got.get("event") == "quit":
                break
        self.assertEqual(got.get("event"), "quit", got)
        self.child.stdin.close()
        self.assertEqual(self.child.wait(timeout=15), 0)
        self.assertFalse(marker.exists())         # 片付けてから終わる

    def test_外枠が閉じれば終わる(self) -> None:
        """**取り残しません。** 窓が消えたら標準入力が閉じ、Python も終わります。"""
        self.wait_ready()
        self.child.stdin.close()
        self.assertEqual(self.child.wait(timeout=15), 0)
        self.assertFalse((Path(self.local) / "runtime" / "desktop.json").exists())


# ==================================================================
# ブラウザ版とデスクトップ版は同時に動かない
# ==================================================================
class 同時に動かない(WebCase):
    """**あとから開いたほうが止まります。** 先に動いているほうは触りません。

    デスクトップ版はポートを使わないので、見分けは印に書いた
    **番号 + プロセスの起動時刻**です(`converter/launch_marker.py`)。
    番号だけで見ると、再起動のあとの使い回しで誤ります。
    """

    def test_デスクトップ版の印を置くと見つかる(self) -> None:
        self.assertIsNone(launch_marker.desktop_running())
        launch_marker.write_desktop(pid=os.getpid(), exe_pid=os.getppid())
        self.addCleanup(launch_marker.remove_desktop)
        found = launch_marker.desktop_running()
        self.assertIsNotNone(found)
        self.assertEqual(found["mode"], "desktop")
        self.assertIn(str(os.getpid()), found["detail"])

    def test_番号が使い回された印は動いていないと見る(self) -> None:
        """PCを落としたあとに残った印で、起動を止めないため。"""
        launch_marker.write_desktop(pid=os.getpid())
        self.addCleanup(launch_marker.remove_desktop)
        path = launch_marker.desktop_path()
        found = json.loads(path.read_text(encoding="utf-8"))
        found["proc_started_at"] = found["proc_started_at"] - 2100
        path.write_text(json.dumps(found), encoding="utf-8")
        self.assertIsNone(launch_marker.desktop_running())

    def test_ブラウザ版の印を置くと見つかる(self) -> None:
        import launch_guard

        self.assertIsNone(launch_marker.browser_running())
        launch_guard.write_lock(launch_guard.new_lock(port=8741, token="t"))
        self.addCleanup(launch_guard.remove_lock)
        found = launch_marker.browser_running()
        self.assertIsNotNone(found)
        self.assertEqual(found["mode"], "browser")

    def test_ブラウザ版はソケットを開かずに見分ける(self) -> None:
        """デスクトップ版の道から呼ぶので、**ポートは叩きません。**"""
        import launch_guard

        launch_guard.write_lock(launch_guard.new_lock(port=8741, token="t"))
        self.addCleanup(launch_guard.remove_lock)
        with mock.patch.object(socket, "socket", _forbidden), \
             mock.patch.object(socket, "create_connection", _forbidden):
            self.assertIsNotNone(launch_marker.browser_running())

    def test_自分と違う側を聞ける(self) -> None:
        launch_marker.write_desktop(pid=os.getpid())
        self.addCleanup(launch_marker.remove_desktop)
        self.assertIsNotNone(launch_marker.other_mode_running("browser"))
        self.assertIsNone(launch_marker.other_mode_running("desktop"))
        with self.assertRaises(ValueError):
            launch_marker.other_mode_running("よそ")

    def test_断りの言葉は1か所から出す(self) -> None:
        title, what = launch_marker.refusal("browser", {})
        self.assertEqual(title, launch_marker.DESKTOP_RUNNING)
        self.assertIn("stop.bat", what)
        title, what = launch_marker.refusal("desktop", {})
        self.assertEqual(title, launch_marker.BROWSER_RUNNING)
        self.assertIn("stop.bat", what)

    def test_ブラウザ版はデスクトップ版が動いていれば起動しない(self) -> None:
        """**窓が開いているのに、ブラウザからもう1つ動かしません。**"""
        import start_app

        launch_marker.write_desktop(pid=os.getpid())
        self.addCleanup(launch_marker.remove_desktop)
        said = []
        with mock.patch.object(start_app, "tell",
                               lambda text, **kw: said.append(text) or False):
            code = start_app.start(open_browser_flag=False)
        self.assertEqual(code, start_app.EXIT_OTHER_MODE)
        self.assertIn(launch_marker.DESKTOP_RUNNING, said[0])

    def test_デスクトップ版はブラウザ版が動いていれば起動しない(self) -> None:
        """`bridge.py` は窓へ `fatal` を知らせて終わります。"""
        import io
        import launch_guard

        launch_guard.write_lock(launch_guard.new_lock(port=8741, token="t"))
        self.addCleanup(launch_guard.remove_lock)
        out = io.BytesIO()
        with mock.patch.object(bridge, "protect_stdout",
                               lambda: out), \
             mock.patch.object(sys, "stdin", mock.Mock(buffer=io.BytesIO())):
            code = bridge.main([])
        self.assertEqual(code, 2)
        events = [json.loads(line) for line in out.getvalue().splitlines()
                  if line.strip()]
        fatal = [e for e in events if e.get("event") == "fatal"]
        self.assertTrue(fatal, events)
        self.assertEqual(fatal[0]["message"], launch_marker.BROWSER_RUNNING)
        self.assertIn("stop.bat", fatal[0]["hint"])
        # **印は置きません**(起動しなかったので)
        self.assertIsNone(launch_marker.read_desktop())


class stopバットは両方を見る(WebCase):
    """`stop.bat` は**動いているほうを止めます**(片方しか居ません)。"""

    def test_デスクトップ版なら窓に閉じてもらう(self) -> None:
        import process_manager

        launch_marker.write_desktop(pid=os.getpid(), exe_pid=os.getppid())
        self.addCleanup(launch_marker.remove_desktop)
        found = launch_marker.desktop_running()
        asked = []
        # 窓に頼むところだけ差し替える(本当に親を終わらせない)
        with mock.patch.object(process_manager.os, "kill",
                               lambda pid, sig: asked.append((pid, sig))), \
             mock.patch.object(process_manager.subprocess, "run",
                               lambda *a, **k: asked.append(a)), \
             mock.patch.object(launch_marker, "desktop_running",
                               side_effect=[found, None]):
            code = process_manager.stop(timeout=1.0)
        self.assertEqual(code, 0)
        self.assertTrue(asked, "窓に閉じるよう頼んでいません")

    def test_状態にデスクトップ版と出る(self) -> None:
        import io
        import process_manager

        launch_marker.write_desktop(pid=os.getpid(), exe_pid=os.getppid())
        self.addCleanup(launch_marker.remove_desktop)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(process_manager.status(), 0)
        text = out.getvalue()
        self.assertIn("デスクトップ版が起動しています", text)
        self.assertIn("ポートを使いません", text)

    def test_窓の番号が使い回されていたら触らない(self) -> None:
        import process_manager

        launch_marker.write_desktop(pid=os.getpid(), exe_pid=os.getppid())
        self.addCleanup(launch_marker.remove_desktop)
        path = launch_marker.desktop_path()
        found = json.loads(path.read_text(encoding="utf-8"))
        found["exe_started_at"] = found["exe_started_at"] - 2100
        path.write_text(json.dumps(found), encoding="utf-8")
        current = launch_marker.desktop_running()
        asked = []
        with mock.patch.object(process_manager.os, "kill",
                               lambda pid, sig: asked.append(pid)), \
             mock.patch.object(process_manager.subprocess, "run",
                               lambda *a, **k: asked.append(a)), \
             mock.patch.object(launch_marker, "desktop_running",
                               side_effect=[current, None]):
            process_manager.stop(timeout=1.0)
        # 窓には触らず、Python にだけ頼む(窓の番号は信じられない)
        self.assertEqual(asked, [os.getpid()])


if __name__ == "__main__":
    unittest.main()
