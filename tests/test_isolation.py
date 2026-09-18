"""テストが本番の記録へ書かないこと

**黙って起きる**たぐいなので機械的に見ます ── ここが外れていても
テストは全部OKのまま、端末の設定とログだけが書き換わります。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._isolation import ENV, ensure_isolated   # noqa: E402

ensure_isolated()


class IsolationTests(unittest.TestCase):
    def test_ローカル領域は一時フォルダを指す(self) -> None:
        from converter import app_config

        root = app_config.local_root().resolve()
        self.assertTrue(
            str(root).startswith(str(Path(tempfile.gettempdir()).resolve())),
            f"本番の領域を指しています: {root}")

    def test_環境変数が立っている(self) -> None:
        self.assertTrue(os.environ.get(ENV))
