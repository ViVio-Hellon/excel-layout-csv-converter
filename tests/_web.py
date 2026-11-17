"""画面とAPIの試験で毎回使う土台

【なぜまとめるか】
`test_web_*.py` はどれも同じ3つを組んでいました。

    1. ローカル領域を一時フォルダへ向ける
    2. 画面の占有・変換・受け取りを**まっさらから**始める
    3. `create_app()` して、合言葉と画面番号を付けて叩く

3つ目が厄介で、合言葉(`X-Tool-Token`)を忘れると 401 が返り、
「なぜか全部断られる」ところから調べることになります。付け忘れられない
ように、ここ1か所に閉じ込めます。

**判断は何もしません。** 組み立ての手順を短く書けるようにするだけです。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from ._isolation import ensure_isolated

ensure_isolated()

#: 試験用の合言葉。**本物と同じ経路を通す**ために必ず付けます
TOKEN = "test-token-abc123"

#: 試験用の停止の合図。**画面には渡らないもの**(stop.bat が持つ側)
STOP_KEY = "test-stop-key-xyz"


class WebCase(unittest.TestCase):
    """1件ごとに、ローカル領域も画面の占有も**まっさらから**。"""

    def setUp(self) -> None:
        from converter import app_config, intake, jobs, screen_lock

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # ローカル領域(設定・ログ・受け取り)と、**配布設定の置き場所**を
        # 1件ごとに新しい一時フォルダへ。配布設定は本物のツールの
        # フォルダに作られるものなので、ここを逃がさないと試験が
        # リポジトリに `配布設定\` を作ります
        self._before = {name: os.environ.get(name)
                        for name in ("XLCSV_LOCAL_DIR", "XLCSV_LAUNCH_DIR")}
        os.environ["XLCSV_LOCAL_DIR"] = str(self.tmp / "local")
        os.environ["XLCSV_LAUNCH_DIR"] = str(self.tmp / "launch")
        (self.tmp / "launch").mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._restore_env)
        app_config.ensure_local_dirs()

        screen_lock.LOCK = screen_lock.ScreenLock()
        jobs.RUNNER = jobs.Runner()
        # 受け取り置き場はタブごとに2つ(変換 / 機側データ集計)
        intake.INTAKE = intake.Intake(intake.CONVERT, intake.SUFFIXES)
        intake.TALLY_INTAKE = intake.Intake(intake.TALLY, intake.TALLY_SUFFIXES)
        self.addCleanup(intake.clear_all)

        import app as app_package
        self.app = app_package.create_app(token=TOKEN, stop_key=STOP_KEY)

    def _restore_env(self) -> None:
        for name, before in self._before.items():
            if before is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = before

    # -- 叩く --------------------------------------------------------
    def request(self, method: str, path: str, payload=None, *,
                screen_id: str = "", body: bytes = b"", headers=None,
                token: str = TOKEN) -> tuple[int, dict]:
        """`app.dispatch` を直接叩く(待ち受けを立てずに受付だけ見る)。"""
        from converter import screen_lock
        from server import Request

        path, _sep, query = path.partition("?")
        raw = json.dumps(payload).encode("utf-8") if payload is not None else body
        head = {"content-type": "application/json", "host": "127.0.0.1"}
        if token:
            head["x-tool-token"] = token
        if screen_id:
            head[screen_lock.HEADER.lower()] = screen_id
        head.update({k.lower(): v for k, v in (headers or {}).items()})
        from urllib.parse import parse_qs

        response = self.app.dispatch(Request(
            method=method, path=path, query=parse_qs(query), body=raw,
            headers=head))
        try:
            return response.status, json.loads(response.body.decode("utf-8"))
        except ValueError:
            return response.status, {}

    # -- よく使う段取り ----------------------------------------------
    def claim(self, tab: str = "tab-A") -> str:
        """画面を1枚受け取る。返るのは画面番号(`X-Screen-Id`)。"""
        status, data = self.request("POST", "/api/screen/claim", {"tab_id": tab})
        self.assertEqual(status, 200, data)
        return data["screen_id"]

    def books(self, count: int = 2) -> Path:
        """読ませるブックを組み立てる(実物は置いていません)。"""
        from tests._fixture import day_sheet, write_xlsx

        folder = self.tmp / "books"
        folder.mkdir(exist_ok=True)
        for n in range(count):
            write_xlsx(folder / f"{n}.xlsx", day_sheet())
        return folder

    def settings(self, screen_id: str, **values) -> dict:
        status, data = self.request("POST", "/api/settings", values,
                                    screen_id=screen_id)
        self.assertEqual(status, 200, data)
        return data

    def wait_for_job(self, seconds: float = 60.0) -> dict:
        from converter import jobs

        limit = time.time() + seconds
        while time.time() < limit:
            snapshot = jobs.RUNNER.snapshot()
            if not snapshot["running"] and snapshot["id"]:
                return snapshot
            time.sleep(0.05)
        self.fail("変換が終わりませんでした")
