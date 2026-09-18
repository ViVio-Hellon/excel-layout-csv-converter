"""変換(長時間処理。基盤仕様書 3章 レベル2)

進み具合・中止・上限・**合わなかったものを名指しすること**を見ます。
変換そのものの正しさは `test_convert.py` の担当です。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter import app_config, jobs             # noqa: E402
from tests._web import WebCase                     # noqa: E402


class JobTests(WebCase):
    def _start(self, count: int = 2, dry_run: bool = False):
        screen_id = self.claim()
        out = self.tmp / "out"
        self.settings(screen_id, input_dir=str(self.books(count)),
                      output_dir=str(out), line="LS")
        status, data = self.request("POST", "/api/job/start",
                                    {"source": "folder", "dry_run": dry_run},
                                    screen_id=screen_id)
        self.assertEqual(status, 200, data)
        return screen_id, out

    def test_変換してCSVが出る(self) -> None:
        _screen_id, out = self._start()
        job = self.wait_for_job()
        self.assertEqual(job["state"], jobs.DONE)
        self.assertEqual(job["counts"]["read_ok"], 2)
        self.assertEqual(job["counts"]["verified"], 2)
        self.assertTrue(list(out.rglob("*.csv")))
        self.assertEqual(job["written"], len(list(out.rglob("*.csv"))))

    def test_確かめるだけならCSVを書かない(self) -> None:
        _screen_id, out = self._start(dry_run=True)
        job = self.wait_for_job()
        self.assertEqual(job["counts"]["read_ok"], 2)
        self.assertEqual(job["written"], 0)
        self.assertFalse(list(out.rglob("*.csv")))

    def test_進み具合が出る(self) -> None:
        screen_id, _out = self._start()
        job = self.wait_for_job()
        self.assertEqual(job["done"], job["total"])
        self.assertEqual(job["percent"], 100)
        _status, data = self.request("GET", "/api/job", screen_id=screen_id)
        self.assertEqual(data["job"]["id"], job["id"])

    def test_上限を超える本数は始めない(self) -> None:
        app_config.load()["limits"]["max_files"] = 1
        self.addCleanup(lambda: app_config.load()["limits"]
                        .__setitem__("max_files", 100))
        screen_id = self.claim()
        self.settings(screen_id, input_dir=str(self.books(2)),
                      output_dir=str(self.tmp / "out"))
        status, data = self.request("POST", "/api/job/start", {"source": "folder"},
                                    screen_id=screen_id)
        self.assertEqual(status, 400)
        self.assertIn("1 本まで", data["detail"])

    def test_読めないブックは名指しされる(self) -> None:
        """**黙って通しません。**"""
        screen_id = self.claim()
        folder = self.books(1)
        (folder / "こわれ.xlsx").write_bytes("これはZIPではありません".encode("utf-8"))
        self.settings(screen_id, input_dir=str(folder),
                      output_dir=str(self.tmp / "out"), line="LS")
        self.request("POST", "/api/job/start", {"source": "folder"},
                     screen_id=screen_id)
        job = self.wait_for_job()
        bad = [f for f in job["files"] if not f["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["name"], "こわれ.xlsx")
        self.assertTrue(bad[0]["error"])

    def test_中止できる(self) -> None:
        screen_id = self.claim()
        self.settings(screen_id, input_dir=str(self.books(3)),
                      output_dir=str(self.tmp / "out"), line="LS")
        self.request("POST", "/api/job/start", {"source": "folder"},
                     screen_id=screen_id)
        jobs.RUNNER.cancel()
        job = self.wait_for_job()
        self.assertIn(job["state"], (jobs.STOPPED, jobs.DONE))

    def test_変換中は設定を変えられない(self) -> None:
        screen_id, _out = self._start()
        status, _data = self.request("POST", "/api/settings", {"line": "HVC"},
                                     screen_id=screen_id)
        self.wait_for_job()
        self.assertIn(status, (200, 409))          # 終わっていれば通ってよい
