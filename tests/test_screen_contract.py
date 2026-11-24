r"""画面とJSの約束 ── **id の打ち間違いを試験で捕まえる**

JS は `document.getElementById("plate-msg")` のように id で要素を引きます。
HTML 側の名前を1字変えても、**JSは静かに何もしません**(`null` に
`addEventListener` した瞬間にそこで止まるか、見た目だけ変わらない)。
画面を開かないと分からないので、ここで突き合わせます。

タブが4つあること・設定がタブの中にあること・パスの欄が広いことも、
ここで約束にしておきます ── 「設定が最初に出る画面」へ戻らないように。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
JS_DIR = ROOT / "app" / "static" / "js"

#: HTML に無くてよい id(JS が作る要素・外枠 `base.html` の持ちもの)
MADE_BY_JS = {"gate", "gate-retry"}


def ids_in_html() -> set[str]:
    found = set(re.findall(r'id="([^"]+)"', HTML))
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    return found | set(re.findall(r'id="([^"]+)"', base))


class IdTests(unittest.TestCase):
    def test_JSが引くidはHTMLにある(self) -> None:
        known = ids_in_html() | MADE_BY_JS
        missing: list[str] = []
        for path in sorted(JS_DIR.rglob("*.js")):
            text = path.read_text(encoding="utf-8")
            for name in re.findall(r'\$\("([a-z0-9_-]+)"\)', text):
                if name not in known:
                    missing.append(f"{path.name}: {name}")
            for name in re.findall(r'getElementById\("([a-z0-9_-]+)"\)', text):
                if name not in known:
                    missing.append(f"{path.name}: {name}")
        self.assertEqual(missing, [], "HTMLに無いidをJSが引いています")

    def test_JSが使う種別はCSSに色がある(self) -> None:
        """サーバが渡す印(`mark-*`)に、`tokens.css` の色が当たっているか。"""
        from converter import plate_graph
        css = (ROOT / "app" / "static" / "css" / "components.css").read_text(
            encoding="utf-8")
        marks = (list(plate_graph.CAT_TONES) + [plate_graph.CAT_REST]
                 + list(plate_graph.SHIFT_CLASSES.values())
                 + [plate_graph.TONE_QTY, plate_graph.TONE_DUR,
                    plate_graph.TONE_STOP, plate_graph.TONE_CUMULATIVE])
        for mark in marks:
            self.assertIn(f".mark-{mark}", css, f"{mark} の色がありません")


class TabTests(unittest.TestCase):
    def test_タブは4つで設定もその1つ(self) -> None:
        tabs = re.findall(r'data-tab="([a-z]+)"', HTML)
        self.assertEqual(tabs, ["convert", "tally", "plate", "settings"])
        for name in tabs:
            self.assertIn(f'id="panel-{name}"', HTML)
        names = (JS_DIR / "tabs.js").read_text(encoding="utf-8")
        self.assertIn('["convert", "tally", "plate", "settings"]', names)

    def test_はじめに出るタブは板集計(self) -> None:
        """いちばんよく開くものを最初に出します(2度目からは前のタブ)。"""
        js = (JS_DIR / "tabs.js").read_text(encoding="utf-8")
        self.assertIn('const FIRST = "plate";', js)
        # JSが動く前の見た目も合わせます(ちらつきを防ぐため)
        self.assertIn('<button class="tab on" id="tab-plate"', HTML)
        self.assertIn('id="panel-plate" role="tabpanel">', HTML)
        self.assertIn('id="panel-convert" role="tabpanel" hidden>', HTML)
        base = (ROOT / "app" / "templates" / "base.html").read_text(
            encoding="utf-8")
        self.assertIn('data-tab="plate"', base)

    def test_設定部は設定タブの中にある(self) -> None:
        """前は上に固定していました。**開いて最初に出るのは「やること」**。"""
        at_settings_panel = HTML.index('id="panel-settings"')
        at_save = HTML.index('id="save"')
        at_convert_panel = HTML.index('id="panel-convert"')
        self.assertLess(at_convert_panel, at_settings_panel)
        self.assertLess(at_settings_panel, at_save)

    def test_進捗はタブで隠すがわる間は出す(self) -> None:
        css = (ROOT / "app" / "static" / "css" / "layout.css").read_text(
            encoding="utf-8")
        self.assertIn('body[data-tab="plate"] #running-area', css)
        self.assertIn("body.busy #running-area { display: block; }", css)
        self.assertIn('id="running-area"', HTML)


class TabOrderTests(unittest.TestCase):
    r"""タブの配線は**いちばん最後**

    `tabs.wire()` は「いまのタブ」を知らせます。先に呼ぶと**誰も聞いて
    いないうちに知らせが飛ぶ**ので、板集計をはじめに出すようにしたとき、
    タブを一度も押さないとラインの一覧も期間も空のままになりました
    (`views/plate.js` がその知らせで読み込みます)。
    """

    def test_タブは画面を組んだあとで配線する(self) -> None:
        js = (JS_DIR / "app.js").read_text(encoding="utf-8")
        self.assertLess(js.index("plate.wire()"), js.index("tabs.wire()"))
        self.assertLess(js.index("settings.wire()"), js.index("tabs.wire()"))


class LotSearchScreenTests(unittest.TestCase):
    """ロットを探すの欄(**必須のものが全部あるか**)。"""

    def test_欄とボタンがそろっている(self) -> None:
        for name in ("lot-start", "lot-end", "lot-no", "lot-line",
                     "lot-run", "lot-csv", "lot-head", "lot-body"):
            self.assertIn(f'id="{name}"', HTML, name)
        self.assertIn("ロットを探す", HTML)
        # 開始日・終了日・ロット№は**必須**の札を出します
        for label in ("開始日", "終了日", "ロット№"):
            at = HTML.index(f">{label}<")
            self.assertIn("必須", HTML[at:at + 60], label)

    def test_共有の日報データの様子を探す前に出す(self) -> None:
        """「設定したのに読めていない」を、探してから気づくのでは遅いので。"""
        self.assertIn('id="plate-shared"', HTML)
        js = (JS_DIR / "views" / "plate.js").read_text(encoding="utf-8")
        self.assertIn("paintShared", js)

    def test_板集計タブの中にある(self) -> None:
        panel = HTML.index('id="panel-plate"')
        after = HTML.index('id="panel-settings"')
        self.assertLess(panel, HTML.index('id="lot-run"') < after and
                        HTML.index('id="lot-run"'))


class FieldTests(unittest.TestCase):
    def test_パスの欄は横いっぱい(self) -> None:
        """共有の道は長いので、2列に並べると先が見えません。"""
        for key in ("input_dir", "output_dir", "ref_dir", "stop_csv",
                    "plate_dir", "log_dir"):
            at = HTML.index(f'id="{key}"')
            opened = HTML.rindex('<label class="field', 0, at)
            self.assertIn("wide", HTML[opened:at], f"{key} の欄が広くありません")
        # ラインは短いので、そのまま(2列に並ぶ)
        at = HTML.index('id="line"')
        opened = HTML.rindex('<label class="field', 0, at)
        self.assertNotIn("wide", HTML[opened:at])
        css = (ROOT / "app" / "static" / "css" / "layout.css").read_text(
            encoding="utf-8")
        self.assertIn(".field.wide { grid-column: 1 / -1; }", css)

    def test_設定の欄とサーバの項目がそろっている(self) -> None:
        from converter import user_settings
        js = (JS_DIR / "views" / "settings.js").read_text(encoding="utf-8")
        for key in user_settings.FIELDS:
            self.assertIn(f'id="{key}"', HTML, f"{key} の欄がありません")
            self.assertIn(f'"{key}"', js, f"{key} をJSが読み書きしません")

    def test_配布設定の操作がそろっている(self) -> None:
        for name in ("dist-export", "dist-reload", "dist-forget",
                     "dist-items", "dist-dir"):
            self.assertIn(f'id="{name}"', HTML)


if __name__ == "__main__":
    unittest.main()
