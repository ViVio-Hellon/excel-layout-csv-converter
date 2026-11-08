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

from converter import app_config, wire              # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
