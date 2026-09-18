"""画面と起動基盤 ── 1枚しか開けないこと・進捗・断り方

【何を確かめるのか】
変換そのものは `test_convert.py` が見ています。ここで見るのは**その周り**:

    ・2枚目の画面を断ること(同じタブの再読込は断らないこと)
    ・占有していない画面からの操作が効かないこと
    ・受け入れ100本の上限
    ・出力先や入力元が決まっていないまま始めないこと
    ・すでに起動しているかの判定(応答で確かめること)

実物のブックは置いていないので、`_fixture.py` が組み立てたものを使います。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

logging.getLogger("nippou").setLevel(logging.CRITICAL)
logging.getLogger("xlcsv.app").setLevel(logging.CRITICAL)

from app import appinfo, intake, jobs, routes, screen, settings_store  # noqa: E402
from tests._fixture import day_sheet, write_xlsx                      # noqa: E402


def request(method: str, path: str, payload=None, token: str = "",
            body: bytes = b"", headers=None) -> tuple[int, dict]:
    """`routes.dispatch` を直接叩く(サーバを上げずに受付だけ見る)。"""
    raw = json.dumps(payload).encode("utf-8") if payload is not None else body
    head = {"content-type": "application/json"}
    if token:
        head["x-screen-token"] = token
    head.update({k.lower(): v for k, v in (headers or {}).items()})
    response = routes.dispatch(routes.Request(
        method=method, path=path, query={}, body=raw, headers=head))
    try:
        return response.status, json.loads(response.body.decode("utf-8"))
    except ValueError:
        return response.status, {}


class Base(unittest.TestCase):
    """1件ごとに、ローカル領域も画面の占有も**まっさらから**。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._before = os.environ.get("XLCSV_LOCAL_DIR")
        os.environ["XLCSV_LOCAL_DIR"] = str(self.tmp / "local")
        self.addCleanup(self._restore_env)
        appinfo.ensure_local_dirs()
        screen.LOCK = screen.ScreenLock()
        jobs.RUNNER = jobs.Runner()
        intake.INTAKE = intake.Intake()
        self.addCleanup(intake.INTAKE.clear)

    def _restore_env(self) -> None:
        if self._before is None:
            os.environ.pop("XLCSV_LOCAL_DIR", None)
        else:
            os.environ["XLCSV_LOCAL_DIR"] = self._before

    def claim(self, tab: str = "tab-A") -> str:
        status, data = request("POST", "/api/screen/claim", {"tab_id": tab})
        self.assertEqual(status, 200)
        return data["token"]

    def books(self, count: int = 2) -> Path:
        folder = self.tmp / "books"
        folder.mkdir(exist_ok=True)
        for n in range(count):
            write_xlsx(folder / f"{n}.xlsx", day_sheet())
        return folder

    def wait_for_job(self, seconds: float = 60.0) -> dict:
        limit = time.time() + seconds
        while time.time() < limit:
            snapshot = jobs.RUNNER.snapshot()
            if not snapshot["running"] and snapshot["id"]:
                return snapshot
            time.sleep(0.05)
        self.fail("変換が終わりませんでした")


# ======================================================================
# 画面は1枚だけ
# ======================================================================
class ScreenTests(Base):
    def test_2枚目は断る(self) -> None:
        self.claim("tab-A")
        status, data = request("POST", "/api/screen/claim", {"tab_id": "tab-B"})
        self.assertEqual(status, 409)
        self.assertIn("すでに別の画面", data["detail"])

    def test_同じタブの再読込は断らない(self) -> None:
        """**再読込で締め出さない。** 読み込みに失敗した画面を開き直せなくなる。"""
        first = self.claim("tab-A")
        second = self.claim("tab-A")
        self.assertNotEqual(first, second)
        self.assertFalse(screen.LOCK.holds(first))
        self.assertTrue(screen.LOCK.holds(second))

    def test_閉じれば空く(self) -> None:
        token = self.claim("tab-A")
        request("POST", "/api/screen/release", {}, token)
        self.assertEqual(request("POST", "/api/screen/claim",
                                 {"tab_id": "tab-B"})[0], 200)

    def test_応答が途切れれば空く(self) -> None:
        """ブラウザが落ちたまま、二度と開けない状態を作らない。"""
        screen.LOCK = screen.ScreenLock(timeout_seconds=0)
        self.claim("tab-A")
        time.sleep(0.01)
        self.assertEqual(request("POST", "/api/screen/claim",
                                 {"tab_id": "tab-B"})[0], 200)

    def test_占有していない画面の操作は効かない(self) -> None:
        self.claim("tab-A")
        for method, path in (("GET", "/api/job"), ("POST", "/api/job/start"),
                             ("POST", "/api/job/cancel"), ("GET", "/api/settings"),
                             ("POST", "/api/input/clear")):
            status, data = request(method, path, {} if method == "POST" else None,
                                   token="よその合図")
            self.assertEqual(status, 409, path)
            self.assertTrue(data.get("screen_lost"), path)

    def test_別のサイトからの書き込みは断る(self) -> None:
        token = self.claim()
        status, _ = request("POST", "/api/job/cancel", {}, token,
                            headers={"Origin": "http://example.invalid"})
        self.assertEqual(status, 403)

    def test_起動確認は画面がなくても答える(self) -> None:
        """待機画面(file://)と多重起動の判定が使うので、ここは開けてあります。"""
        status, data = request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["app_id"], appinfo.app_id())


# ======================================================================
# 設定部
# ======================================================================
class SettingsTests(Base):
    def test_保存して読み直せる(self) -> None:
        token = self.claim()
        status, data = request("POST", "/api/settings",
                               {"output_dir": '"' + str(self.tmp / "out") + '"',
                                "line": "LS"}, token)
        self.assertEqual(status, 200)
        # 引用符つきで貼られても、そのまま使える形に直す
        self.assertEqual(data["settings"]["output_dir"], str(self.tmp / "out"))
        self.assertEqual(request("GET", "/api/settings", token=token)[1]
                         ["settings"]["line"], "LS")

    def test_どこを指しているか返す(self) -> None:
        token = self.claim()
        _status, data = request("POST", "/api/settings",
                                {"input_dir": str(self.books())}, token)
        found = {p["key"]: p for p in data["paths"]}
        self.assertTrue(found["input_dir"]["exists"])
        self.assertFalse(found["output_dir"]["exists"])

    def test_出力先が無ければ始めない(self) -> None:
        token = self.claim()
        request("POST", "/api/settings", {"input_dir": str(self.books())}, token)
        status, data = request("POST", "/api/job/start",
                               {"source": "folder"}, token)
        self.assertEqual(status, 400)
        self.assertIn("出力先", data["detail"])

    def test_入力フォルダが無ければ始めない(self) -> None:
        token = self.claim()
        request("POST", "/api/settings",
                {"input_dir": str(self.tmp / "ない"),
                 "output_dir": str(self.tmp / "out")}, token)
        status, data = request("POST", "/api/job/start",
                               {"source": "folder"}, token)
        self.assertEqual(status, 400)
        self.assertIn("入力フォルダ", data["detail"])


# ======================================================================
# 受け取り(ドラッグ投入)
# ======================================================================
class IntakeTests(Base):
    def test_受け取って数える(self) -> None:
        token = self.claim()
        request("POST", "/api/input/begin", {}, token)
        data = (self.tmp / "a.xlsx")
        write_xlsx(data, day_sheet())
        status, answer = request("POST", "/api/input/add", None, token,
                                 body=data.read_bytes(),
                                 headers={"X-Filename": "a.xlsx"})
        self.assertEqual(status, 200)
        self.assertEqual(answer["count"], 1)

    def test_xlsx以外は受けない(self) -> None:
        token = self.claim()
        request("POST", "/api/input/begin", {}, token)
        _status, answer = request("POST", "/api/input/add", None, token,
                                  body=b"x", headers={"X-Filename": "memo.txt"})
        self.assertFalse(answer["ok"])
        self.assertIn(".xlsx", answer["message"])

    def test_上限を超えたら受けない(self) -> None:
        """**100本まで。** 超えたぶんは黙って捨てず、断りを返します。"""
        limit = appinfo.load()["limits"]["max_files"]
        appinfo.load()["limits"]["max_files"] = 2
        self.addCleanup(lambda: appinfo.load()["limits"].__setitem__("max_files", limit))
        token = self.claim()
        request("POST", "/api/input/begin", {}, token)
        source = self.tmp / "a.xlsx"
        write_xlsx(source, day_sheet())
        for _ in range(2):
            request("POST", "/api/input/add", None, token,
                    body=source.read_bytes(), headers={"X-Filename": "a.xlsx"})
        _status, answer = request("POST", "/api/input/add", None, token,
                                  body=source.read_bytes(),
                                  headers={"X-Filename": "a.xlsx"})
        self.assertFalse(answer["ok"])
        self.assertIn("2 本まで", answer["message"])

    def test_同じ名前が2つ来ても上書きしない(self) -> None:
        token = self.claim()
        request("POST", "/api/input/begin", {}, token)
        source = self.tmp / "a.xlsx"
        write_xlsx(source, day_sheet())
        for _ in range(2):
            request("POST", "/api/input/add", None, token,
                    body=source.read_bytes(), headers={"X-Filename": "同じ.xlsx"})
        self.assertEqual(intake.INTAKE.count(), 2)

    def test_名前に区切り文字が入っていても外へ書かない(self) -> None:
        self.assertEqual(intake.safe_name(r"..\..\狙い.xlsx"), "狙い.xlsx")
        self.assertEqual(intake.safe_name("2026.08/機側.xlsx"), "機側.xlsx")

    def test_数えるだけ(self) -> None:
        token = self.claim()
        status, data = request("POST", "/api/input/scan",
                               {"folder": str(self.books(3))}, token)
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 3)

    def test_数えて多すぎれば先に言う(self) -> None:
        """押してから断られるより、押す前に分かるほうがよい。"""
        appinfo.load()["limits"]["max_files"] = 1
        self.addCleanup(lambda: appinfo.load()["limits"].__setitem__("max_files", 100))
        token = self.claim()
        _status, data = request("POST", "/api/input/scan",
                                {"folder": str(self.books(2))}, token)
        self.assertTrue(data["over"])
        self.assertIn("1 本までです", data["message"])


# ======================================================================
# 変換(長時間処理)
# ======================================================================
class JobTests(Base):
    def _setup(self, count: int = 2, dry_run: bool = False) -> tuple[str, Path]:
        token = self.claim()
        out = self.tmp / "out"
        request("POST", "/api/settings",
                {"input_dir": str(self.books(count)),
                 "output_dir": str(out), "line": "LS"}, token)
        status, data = request("POST", "/api/job/start",
                               {"source": "folder", "dry_run": dry_run}, token)
        self.assertEqual(status, 200, data)
        return token, out

    def test_変換してCSVが出る(self) -> None:
        _token, out = self._setup()
        job = self.wait_for_job()
        self.assertEqual(job["state"], jobs.DONE)
        self.assertEqual(job["counts"]["read_ok"], 2)
        self.assertEqual(job["counts"]["verified"], 2)
        self.assertTrue(list(out.rglob("*.csv")))
        self.assertEqual(job["written"], len(list(out.rglob("*.csv"))))

    def test_確かめるだけならCSVを書かない(self) -> None:
        _token, out = self._setup(dry_run=True)
        job = self.wait_for_job()
        self.assertEqual(job["counts"]["read_ok"], 2)
        self.assertEqual(job["written"], 0)
        self.assertFalse(list(out.rglob("*.csv")))

    def test_進み具合が出る(self) -> None:
        token, _out = self._setup()
        job = self.wait_for_job()
        self.assertEqual(job["done"], job["total"])
        self.assertEqual(job["percent"], 100)
        self.assertEqual(request("GET", "/api/job", token=token)[1]["job"]["id"],
                         job["id"])

    def test_2つ同時には始めない(self) -> None:
        token, _out = self._setup()
        status, data = request("POST", "/api/job/start", {"source": "folder"}, token)
        self.wait_for_job()
        if status == 400:                          # まだ走っていれば断られる
            self.assertIn("変換中", data["detail"])

    def test_上限を超える本数は始めない(self) -> None:
        appinfo.load()["limits"]["max_files"] = 1
        self.addCleanup(lambda: appinfo.load()["limits"].__setitem__("max_files", 100))
        token = self.claim()
        request("POST", "/api/settings",
                {"input_dir": str(self.books(2)),
                 "output_dir": str(self.tmp / "out")}, token)
        status, data = request("POST", "/api/job/start", {"source": "folder"}, token)
        self.assertEqual(status, 400)
        self.assertIn("1 本まで", data["detail"])

    def test_変換中は設定を変えられない(self) -> None:
        token, _out = self._setup(count=2)
        status, _data = request("POST", "/api/settings", {"line": "HVC"}, token)
        self.wait_for_job()
        self.assertIn(status, (200, 409))          # 終わっていれば通ってよい

    def test_読めないブックは名指しされる(self) -> None:
        token = self.claim()
        folder = self.books(1)
        (folder / "こわれ.xlsx").write_bytes("これはZIPではありません".encode("utf-8"))
        request("POST", "/api/settings",
                {"input_dir": str(folder), "output_dir": str(self.tmp / "out"),
                 "line": "LS"}, token)
        request("POST", "/api/job/start", {"source": "folder"}, token)
        job = self.wait_for_job()
        bad = [f for f in job["files"] if not f["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["name"], "こわれ.xlsx")
        self.assertTrue(bad[0]["error"])

    def test_中止できる(self) -> None:
        token = self.claim()
        request("POST", "/api/settings",
                {"input_dir": str(self.books(3)), "output_dir": str(self.tmp / "out"),
                 "line": "LS"}, token)
        request("POST", "/api/job/start", {"source": "folder"}, token)
        jobs.RUNNER.cancel()
        job = self.wait_for_job()
        self.assertIn(job["state"], (jobs.STOPPED, jobs.DONE))


# ======================================================================
# 開く先を選ばせない
# ======================================================================
class OpenTests(Base):
    def test_知らない場所は開かない(self) -> None:
        token = self.claim()
        status, _data = request("POST", "/api/open", {"what": "C:\\Windows"}, token)
        self.assertEqual(status, 400)


# ======================================================================
# 停止
# ======================================================================
class ShutdownTests(Base):
    def test_出どころを確かめられなければ止めない(self) -> None:
        routes.RUNTIME.stop_key = "ほんもの"
        self.addCleanup(lambda: setattr(routes.RUNTIME, "stop_key", ""))
        status, _data = request("POST", "/api/shutdown", {"stop_key": "にせもの"})
        self.assertEqual(status, 403)
        self.assertFalse(routes.RUNTIME.shutdown.is_set())

    def test_合図があれば止まる(self) -> None:
        routes.RUNTIME.stop_key = "ほんもの"
        routes.RUNTIME.shutdown.clear()
        self.addCleanup(routes.RUNTIME.shutdown.clear)
        self.addCleanup(lambda: setattr(routes.RUNTIME, "stop_key", ""))
        status, _data = request("POST", "/api/shutdown", {"stop_key": "ほんもの"})
        self.assertEqual(status, 200)
        self.assertTrue(routes.RUNTIME.shutdown.is_set())


# ======================================================================
# 多重起動の判定
# ======================================================================
class GuardTests(Base):
    def test_空いていれば起動してよい(self) -> None:
        import launch_guard
        self.assertEqual(launch_guard.probe().state, launch_guard.FREE)

    def test_別のアプリが使っていたら見分ける(self) -> None:
        """**HTTPが返ったことと、うちが起動していることは別。**"""
        import launch_guard

        class Other(BaseHTTPRequestHandler):
            def do_GET(self):                      # noqa: N802
                body = json.dumps({"app_id": "よそのアプリ",
                                   "name": "別のツール"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):           # noqa: A003
                pass

        httpd = ThreadingHTTPServer((appinfo.host(), appinfo.port()), Other)
        Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        found = launch_guard.probe()
        self.assertEqual(found.state, launch_guard.FOREIGN)
        self.assertIn("別のアプリ", launch_guard.refusal_text(found))


if __name__ == "__main__":
    unittest.main()
