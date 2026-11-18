r"""起動ファイル(.vbs / .bat)の作りを固定する

**Linux上で編集して、Windowsで実行する**という組み合わせなので、文字
コードと改行はここで機械的に押さえておかないと静かに壊れます。壊れたこと
は現場で「ダブルクリックしても何も起きない」として現れ、原因にたどり着く
までに時間がかかります。

【なぜ CP932 なのか】
- `.bat` は **cmd.exe がコンソールのコードページで解釈します**。
  日本語Windowsの既定は932なので、ファイルもCP932でなければなりません。
- `.vbs` は **WSH がシステムANSIコードページで読みます**。同じく932です。

【CP932 で特に危ないもの】
CP932の2バイト目には `\`(0x5C)や `@`(0x40)がそのまま現れます。
コードページがずれた状態で読まれると、`表`(0x95 5C)や `ソ`(0x83 5C)が
化けて、表示だけでなく**解釈まで変わります**。だから `chcp 932` は、
非ASCIIが出てくるより前に置きます。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 起動ファイルはこの3本だけ。役割が違うものだけを残しています:
#
#   Start.vbs … 通常起動。**利用者がダブルクリックするのはこれ1つ**
#   start.bat … 診断起動(コンソールを開いたまま経過を出す)
#   stop.bat  … 停止
LAUNCH_FILES = ("Start.vbs", "start.bat", "stop.bat")


class LaunchFileTests(unittest.TestCase):
    def test_この3本だけ(self) -> None:
        """**押せるものを増やさない**(基盤仕様書 2.1)。"""
        found = sorted(p.name for p in ROOT.iterdir()
                       if p.suffix.lower() in (".bat", ".vbs", ".cmd"))
        self.assertEqual(found, sorted(LAUNCH_FILES))

    def test_CP932で保存されている(self) -> None:
        for name in LAUNCH_FILES:
            raw = (ROOT / name).read_bytes()
            with self.subTest(name=name):
                raw.decode("cp932")               # 読めなければここで落ちる
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"),
                                 "BOM付きUTF-8で保存されています")
                if any(b > 0x7F for b in raw):
                    with self.assertRaises(UnicodeDecodeError,
                                           msg=f"{name} がUTF-8で保存されています"):
                        raw.decode("utf-8")

    def test_改行はCRLF(self) -> None:
        for name in LAUNCH_FILES:
            raw = (ROOT / name).read_bytes()
            with self.subTest(name=name):
                self.assertNotIn(b"\r\n", raw.replace(b"\r\n", b""))
                self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"),
                                 "LFだけの行があります")

    def test_gitattributesが改行を固定している(self) -> None:
        """`core.autocrlf` の既定に左右されないようにする。"""
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.bat text eol=crlf", text)
        self.assertIn("*.vbs text eol=crlf", text)

    def test_batはchcpより前をASCIIに保つ(self) -> None:
        """コードページを変える前の非ASCIIは、その時点で化けます。"""
        for name in ("start.bat", "stop.bat"):
            raw = (ROOT / name).read_bytes()
            with self.subTest(name=name):
                index = raw.find(b"chcp 932")
                self.assertGreater(index, -1, "chcp 932 がありません")
                self.assertTrue(all(b <= 0x7F for b in raw[:index]),
                                "chcp より前に非ASCIIがあります")

    def test_batは共有フォルダでも動く(self) -> None:
        """`\\\\サーバ\\...` は現在地にできないので `pushd` を使う。"""
        for name in ("start.bat", "stop.bat"):
            text = (ROOT / name).read_text(encoding="cp932")
            with self.subTest(name=name):
                self.assertIn('pushd "%~dp0"', text)
                self.assertNotIn('cd /d "%~dp0"', text)

    def test_batは引数をそのまま渡す(self) -> None:
        """`start.bat --check` / `stop.bat --force` が効くように。"""
        start = (ROOT / "start.bat").read_text(encoding="cp932")
        stop = (ROOT / "stop.bat").read_text(encoding="cp932")
        self.assertIn("start_app.py --diagnose %*", start)
        self.assertIn("process_manager.py %*", stop)

    def test_vbsは本体の在り処を確かめてから起動する(self) -> None:
        """**pythonw はコンソールを持たない** ── 無いと本当に何も起きない。"""
        text = (ROOT / "Start.vbs").read_text(encoding="cp932")
        self.assertIn("Option Explicit", text)
        self.assertIn("start_app.py", text)
        self.assertIn("FileExists", text)
        self.assertIn("pythonw", text)
        self.assertIn("MsgBox", text)

    def test_呼ぶ先が実在する(self) -> None:
        for name in ("start_app.py", "process_manager.py"):
            self.assertTrue((ROOT / name).is_file(), name)


class DistributionListTests(unittest.TestCase):
    r"""配るもの(Actions の「フォルダまるごと」)に**取りこぼしが無いか**

    配るのは exe 1つではありません ── 画面も変換も Python 側にあります。
    新しい exe だけを古いフォルダへ入れると、窓は新しいのに中身は古いまま
    になり、**画面には古い版が出ます**(1.13.2 の exe でそう見えました)。

    取りこぼすと「配ったのに起動しない」になるので、**根に置いてある
    モジュールが全部入っているか**をここで見ます。
    """

    ROOT = Path(__file__).resolve().parent.parent
    WORKFLOW = ROOT / ".github" / "workflows" / "デスクトップ版をつくる.yml"

    #: 配らないもの(配った先では使いません)
    NOT_SHIPPED = {"stop.py"}

    def test_根のモジュールは全部配る(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        missing = []
        for path in sorted(self.ROOT.glob("*.py")):
            if path.name in self.NOT_SHIPPED:
                continue
            if f'"{path.name}"' not in text:
                missing.append(path.name)
        self.assertEqual(missing, [],
                         "配るものの一覧に入っていません"
                         "(.github/workflows/デスクトップ版をつくる.yml)")

    def test_フォルダも全部配る(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        for name in ("app", "converter", "config", "vendor", "docs"):
            self.assertIn(f'"{name}"', text, f"{name} を配っていません")

    def test_起動するものと止めるものが入っている(self) -> None:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        for name in ("Start.vbs", "start.bat", "stop.bat",
                     "ExcelCsvConverter.exe"):
            self.assertIn(name, text, f"{name} を配っていません")
