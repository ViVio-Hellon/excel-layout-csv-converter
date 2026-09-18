"""アプリ固有値(`config/app.json`)の読み取り

**ここが唯一の出どころ**なので、既定へ落ちたときに黙らないことまで見ます
(基盤仕様書 5.2)。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ensure_isolated       # noqa: E402

ensure_isolated()

from converter import app_config                   # noqa: E402


class AppConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        app_config.load(force=True)
        self.addCleanup(app_config.load, force=True)

    def test_配るときに決める値が読める(self) -> None:
        self.assertTrue(app_config.app_id())
        self.assertTrue(app_config.display_name())
        self.assertEqual(app_config.monitor_level(), 2)
        self.assertEqual(app_config.max_files(), 100)
        self.assertTrue(app_config.base_url().startswith("http://127.0.0.1:"))

    def test_版の書き方がおかしければ言う(self) -> None:
        """起動は止めません。**版が読めなくても業務はできます。**"""
        self.assertEqual(app_config.version_problem(), "")
        app_config.load()["version"] = "2026春"
        self.assertIn("数字3つ", app_config.version_problem())

    def test_設定が壊れていても起動はできる(self) -> None:
        """**起動して画面に理由を出す**ほうが、起動しないより調べやすい。"""
        broken = Path(tempfile.mkdtemp()) / "app.json"
        broken.write_text("{壊れています", encoding="utf-8")
        before = app_config.CONFIG_PATH
        app_config.CONFIG_PATH = broken
        self.addCleanup(setattr, app_config, "CONFIG_PATH", before)
        app_config.load(force=True)
        self.assertTrue(app_config.app_id())        # 既定で動く
        self.assertIn("読めませんでした", app_config.load_error())

    def test_設定が無くても起動はできる(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "ない.json"
        before = app_config.CONFIG_PATH
        app_config.CONFIG_PATH = missing
        self.addCleanup(setattr, app_config, "CONFIG_PATH", before)
        app_config.load(force=True)
        self.assertIn("ありません", app_config.load_error())

    def test_書き忘れたキーは既定で埋まる(self) -> None:
        partial = Path(tempfile.mkdtemp()) / "app.json"
        partial.write_text(json.dumps({"display_name": "試験"}), encoding="utf-8")
        before = app_config.CONFIG_PATH
        app_config.CONFIG_PATH = partial
        self.addCleanup(setattr, app_config, "CONFIG_PATH", before)
        app_config.load(force=True)
        self.assertEqual(app_config.display_name(), "試験")
        self.assertEqual(app_config.max_files(), 100)

    def test_ローカル領域は名前で取る(self) -> None:
        self.assertTrue(str(app_config.local_dir("logs")).endswith("logs"))
        with self.assertRaises(ValueError):
            app_config.local_dir("どこか")

    def test_診断の1枚に要るものが出る(self) -> None:
        text = app_config.describe()
        for word in ("アプリID", "待ち受け", "ローカル領域", "受け入れ上限"):
            self.assertIn(word, text)
