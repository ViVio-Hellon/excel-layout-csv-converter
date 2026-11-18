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


class VersionAgreementTests(unittest.TestCase):
    """**版は3か所に書いてあります。** 食い違ったままにしません

        config/app.json          画面の帯と診断に出る版
        src-tauri/tauri.conf.json 窓(デスクトップ版)の版
        src-tauri/Cargo.toml     exe の版(Windowsのプロパティに出る)
        src-tauri/Cargo.lock     ↑を書き写したもの(**忘れると CI が止まります**)

    どれか1つを上げ忘れると、「画面は 1.12.0 なのに exe は 1.11.3」に
    なり、どちらが配られているのか分からなくなります。
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _json_version(self, name: str) -> str:
        return json.loads((self.ROOT / name).read_text(encoding="utf-8"))["version"]

    def _cargo_version(self) -> str:
        for line in (self.ROOT / "src-tauri" / "Cargo.toml") \
                .read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
        return ""

    def _lock_version(self) -> str:
        """`Cargo.lock` に書かれた、このツール自身の版。

        `cargo` は `Cargo.toml` の版を lock にも写します。上げ忘れると
        **`cargo test --locked` が「lock を更新できません」で止まります**
        (CI が1度これで赤くなりました)。
        """
        text = (self.ROOT / "src-tauri" / "Cargo.lock").read_text(
            encoding="utf-8")
        at = text.index('name = "excel-csv-converter-desktop"')
        for line in text[at:].splitlines()[1:4]:
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip('"')
        return ""

    def test_版がそろっている(self) -> None:
        app = self._json_version("config/app.json")
        self.assertRegex(app, r"^\d+\.\d+\.\d+$")
        self.assertEqual(self._json_version("src-tauri/tauri.conf.json"), app)
        self.assertEqual(self._cargo_version(), app)
        self.assertEqual(self._lock_version(), app)


class ExeVersionTests(unittest.TestCase):
    r"""**exe と中身の版が違えば言います**

    配るのはフォルダまるごとですが、exe だけを古いフォルダへ入れることが
    できてしまいます。そのとき画面に出るのは中身の版なので、「exe が
    古い」に見えます ── 本当は中身が古いのです。気づけるように言います。
    """

    def setUp(self) -> None:
        app_config.load(force=True)
        self.addCleanup(app_config.load, force=True)
        self._before = os.environ.get("XLCSV_EXE_VERSION")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._before is None:
            os.environ.pop("XLCSV_EXE_VERSION", None)
        else:
            os.environ["XLCSV_EXE_VERSION"] = self._before

    def test_ブラウザ版では何も言わない(self) -> None:
        os.environ.pop("XLCSV_EXE_VERSION", None)
        self.assertEqual(app_config.exe_version(), "")
        self.assertEqual(app_config.version_note(), "")

    def test_同じ版なら何も言わない(self) -> None:
        os.environ["XLCSV_EXE_VERSION"] = app_config.version()
        self.assertEqual(app_config.version_note(), "")

    def test_違えば両方の版を出す(self) -> None:
        os.environ["XLCSV_EXE_VERSION"] = "9.9.9"
        note = app_config.version_note()
        self.assertIn("VER9.9.9", note)
        self.assertIn(f"VER{app_config.version()}", note)
        self.assertIn("まるごと", note)            # どうすればよいかまで
        # 診断情報にも出します
        self.assertIn("窓(exe)", app_config.describe())

    def test_窓が渡す版はexeの版そのもの(self) -> None:
        """Rust 側が `CARGO_PKG_VERSION` を渡しています。"""
        rust = (Path(__file__).resolve().parent.parent / "src-tauri" / "src"
                / "bridge.rs").read_text(encoding="utf-8")
        self.assertIn('.env("XLCSV_EXE_VERSION", env!("CARGO_PKG_VERSION"))',
                      rust)
