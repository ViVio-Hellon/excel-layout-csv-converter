r"""配布設定 ── 1台で決めた設定を、配った先の端末でそのまま使う

肝は**上書きしない**ことです。

    端末で打った値        … そのまま(配布設定より強い)
    まだ決まっていない値  … 配布設定で埋める
    前に配った値のまま    … 配り直した新しい値に置き換える
    「読み込み直す」      … 端末で決めた値も揃える

空の値を「無い」と数えるのも要点です ── 「既定に戻す」で空になった
項目を既存と数えると、配ったのに前のまま動く端末ができます。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter import distribution, user_settings         # noqa: E402
from tests._web import WebCase                            # noqa: E402


class ExportTests(WebCase):
    def test_起動用のファイルと同じ階層へ書き出す(self) -> None:
        user_settings.save({"output_dir": r"D:\CSV出力",
                            "plate_dir": r"\\日報管理\集計CSV・印刷用HTML"})
        result = distribution.export()
        self.assertTrue(result["ok"])
        path = distribution.settings_path()
        self.assertEqual(path.parent.parent, self.tmp / "launch")
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["format"], distribution.FORMAT)
        self.assertEqual(raw["settings"]["output_dir"], r"D:\CSV出力")
        self.assertIn("plate_dir", raw["settings"])
        # 何が入っているかを人が読める形でも置きます
        readme = (distribution.folder() / distribution.README_NAME)
        self.assertIn("板データ参照パス", readme.read_text(encoding="utf-8"))

    def test_空の項目は配らない(self) -> None:
        """空を配ると、配った先の「まだ決まっていない」と区別が付きません。"""
        user_settings.save({"output_dir": r"D:\出力", "ref_dir": ""})
        distribution.export()
        raw = json.loads(distribution.settings_path().read_text(encoding="utf-8"))
        self.assertNotIn("ref_dir", raw["settings"])

    def test_項目を選んで配れる(self) -> None:
        user_settings.save({"output_dir": r"D:\出力", "line": "L-1"})
        distribution.export(["line"])
        raw = json.loads(distribution.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(list(raw["settings"]), ["line"])

    def test_配る値が無ければ書かない(self) -> None:
        result = distribution.export()
        self.assertFalse(result["ok"])
        self.assertIn("配る値がありません", result["message"])
        self.assertFalse(distribution.settings_path().exists())

    def test_消すのはこちらが作ったものだけ(self) -> None:
        user_settings.save({"output_dir": r"D:\出力"})
        distribution.export()
        mine = distribution.folder() / "人が置いたもの.txt"
        mine.write_text("のこす", encoding="utf-8")
        result = distribution.forget()
        self.assertTrue(result["ok"])
        self.assertFalse(distribution.settings_path().exists())
        self.assertTrue(mine.is_file())            # 人の物は消しません
        # 端末の設定はそのまま
        self.assertEqual(user_settings.load().output_dir, r"D:\出力")


class ApplyTests(WebCase):
    def put(self, values: dict, *, fmt: int | None = None) -> None:
        """配る側が書いた `配布設定\\設定.json` を置く。"""
        folder = distribution.folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / distribution.SETTINGS_NAME).write_text(
            json.dumps({"format": distribution.FORMAT if fmt is None else fmt,
                        "settings": values}, ensure_ascii=False),
            encoding="utf-8")

    def test_まだ決まっていない項目だけ埋める(self) -> None:
        user_settings.save({"output_dir": r"D:\端末で打った"})
        self.put({"output_dir": r"\\共有\配った", "plate_dir": r"\\共有\板"})
        applied = distribution.apply_on_start()
        settings = user_settings.load()
        self.assertEqual(settings.output_dir, r"D:\端末で打った")   # 上書きしない
        self.assertEqual(settings.plate_dir, r"\\共有\板")          # 埋める
        self.assertEqual(applied, ["plate_dir"])

    def test_空の値は無いと数える(self) -> None:
        user_settings.save({"plate_dir": ""})
        self.put({"plate_dir": r"\\共有\板"})
        distribution.apply_on_start()
        self.assertEqual(user_settings.load().plate_dir, r"\\共有\板")

    def test_配り直すと前に配った値は置き換わる(self) -> None:
        self.put({"plate_dir": r"\\共有\板"})
        distribution.apply_on_start()
        self.put({"plate_dir": r"\\共有\板あたらしい"})
        applied = distribution.apply_on_start()
        self.assertEqual(applied, ["plate_dir"])
        self.assertEqual(user_settings.load().plate_dir, r"\\共有\板あたらしい")

    def test_読み込んだあと端末で直した値は残る(self) -> None:
        self.put({"plate_dir": r"\\共有\板"})
        distribution.apply_on_start()
        user_settings.save({"plate_dir": r"D:\自分で直した"})
        self.put({"plate_dir": r"\\共有\板あたらしい"})
        self.assertEqual(distribution.apply_on_start(), [])
        self.assertEqual(user_settings.load().plate_dir, r"D:\自分で直した")

    def test_読み込み直すと端末の値も揃える(self) -> None:
        user_settings.save({"output_dir": r"D:\端末で打った"})
        self.put({"output_dir": r"\\共有\配った"})
        result = distribution.reload_now()
        self.assertTrue(result["ok"])
        self.assertEqual(user_settings.load().output_dir, r"\\共有\配った")

    def test_配布設定が無ければ何もしない(self) -> None:
        self.assertEqual(distribution.apply_on_start(), [])
        self.assertFalse(distribution.reload_now()["ok"])

    def test_壊れていても起動は止めない(self) -> None:
        folder = distribution.folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / distribution.SETTINGS_NAME).write_text("{壊れている",
                                                         encoding="utf-8")
        self.assertEqual(distribution.apply_on_start(), [])
        self.assertIn("読めません", distribution.state()["problem"])

    def test_版が違えば読まない(self) -> None:
        self.put({"plate_dir": r"\\共有\板"}, fmt=99)
        self.assertEqual(distribution.apply_on_start(), [])
        self.assertIn("版が違います", distribution.state()["problem"])

    def test_起動のときに読んでから写しへ渡す(self) -> None:
        self.put({"ref_dir": str(self.tmp / "マスタ")})
        applied = distribution.boot()
        self.assertEqual(applied, ["ref_dir"])
        import os
        self.assertEqual(os.environ.get("NIPPOU_GW_REF_DIR"),
                         str(self.tmp / "マスタ"))

    def test_どの値が配布設定から来たか言える(self) -> None:
        self.put({"plate_dir": r"\\共有\板"})
        distribution.apply_on_start()
        state = distribution.state()
        self.assertIn("plate_dir", state["from_distribution"])
        user_settings.save({"plate_dir": r"D:\自分で"})
        self.assertNotIn("plate_dir", distribution.state()["from_distribution"])


class RouteTests(WebCase):
    def test_設定の画面に配布設定の様子が出る(self) -> None:
        _status, data = self.request("GET", "/api/settings",
                                     screen_id=self.claim())
        self.assertIn("distribution", data)
        self.assertEqual(data["distribution"]["dir"],
                         str(self.tmp / "launch" / distribution.DIR_NAME))
        self.assertFalse(data["distribution"]["exists"])
        keys = [one["key"] for one in data["distribution"]["items"]]
        self.assertIn("plate_dir", keys)

    def ask(self, body: dict):
        return self.request("POST", "/api/distribution", body,
                            screen_id=self.claim())

    def test_画面から書き出して読み込み直せる(self) -> None:
        user_settings.save({"output_dir": r"D:\出力"})
        status, data = self.ask({"what": "export"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["distribution"]["exists"])

        user_settings.save({"output_dir": r"D:\べつの"})
        _status, data = self.ask({"what": "reload"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["settings"]["output_dir"], r"D:\出力")

        _status, data = self.ask({"what": "forget"})
        self.assertTrue(data["ok"])
        self.assertFalse(data["distribution"]["exists"])

    def test_知らない操作は断る(self) -> None:
        status, _data = self.ask({"what": "なにか"})
        self.assertEqual(status, 400)

    def test_変換中は変えられない(self) -> None:
        from converter import jobs
        before = jobs.RUNNER.job.state
        jobs.RUNNER.job.state = jobs.RUNNING
        self.addCleanup(setattr, jobs.RUNNER.job, "state", before)
        status, data = self.ask({"what": "export"})
        self.assertEqual(status, 409)
        self.assertIn("変換中", data["reason"])
