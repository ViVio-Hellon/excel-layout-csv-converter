r"""ドラッグで受け取ったブックの置き場所

【なぜ一度こちらへ置くのか】
ブラウザは、放り込まれたファイルの**本当の場所(フルパス)を教えません**。
安全のための決まりで、社内アプリでも変えられません。渡されるのは中身だけ
です。そのため、受け取った中身をこの端末のローカル領域へ書き出してから、
フォルダを指定したときとまったく同じ道で読みます。

    ドラッグ → %LOCALAPPDATA%\…\work\intake\ へ写す → converter.run が読む

**出したCSVをダウンロードさせることはありません。** 出力先は設定部で
指定したフォルダで、書くのはこの端末のPythonです ── ブラウザの
ダウンロードフォルダに散らばると、どれが最新か分からなくなります。

【置いたものは残しません】
受け取ったブックは一時のものなので `work` に置きます(基盤仕様書 4.6)。
次に受け取るとき、起動したとき、そして片づけを頼まれたときに消します。

【デスクトップ版は写しません ── **本当の場所をそのまま読みます**】(1.12.0)
ブラウザは本当の場所を教えてくれませんが、**デスクトップ版の窓(Tauri)は
教えてくれます**。写す必要が無いので:

    ブラウザ版     中身を送ってもらう → `work\intake` へ写す → 読む(1本 50MB まで)
    デスクトップ版 **本当の場所を受け取る → その場で読む**(写さない・大きさの上限なし)

`use_paths()` がその入口です。写していないので、**片づけでも消しません**
(消すのは `work\intake` に写したものだけ ── 人のブックを消しません)。
"""
from __future__ import annotations

import re
import shutil
import threading
import unicodedata
from pathlib import Path

from . import app_config
from .logging_utils import get_logger

log = get_logger("intake")

#: 変換が読める拡張子。`converter.run.SUFFIXES` と同じもの
SUFFIXES = (".xlsx", ".xlsm")

#: 機側データ集計が読める拡張子。**CSVも受けます**
#: (変換で出したCSVや、シートをCSVで保存したものを積み増せるように)
TALLY_SUFFIXES = (".xlsx", ".xlsm", ".csv")

#: ファイル名から落とす字。**区切り文字は落とします** ── 受け取った名前で
#: 上の階層へ書かれないようにするため
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


#: 受け取り置き場の名前。**用途ごとに分けます** ── 変換用と集計用を同じ
#: 場所に置くと、片方を片づけたときにもう片方まで消えます
CONVERT = "convert"
TALLY = "tally"


def folder(kind: str = CONVERT) -> Path:
    return app_config.local_dir("work") / "intake" / kind


def safe_name(name: str) -> str:
    """渡された名前を、この端末で使える形にする。

    フォルダ区切りは**名前の一部として渡ってくることがある**ので
    (フォルダごと放り込んだ場合)、最後の1つだけを採ります。
    """
    text = unicodedata.normalize("NFC", (name or "").strip())
    text = text.replace("\\", "/").split("/")[-1]
    text = _BAD.sub("_", text).lstrip(".")
    return text[:120] or "無題.xlsx"


class Intake:
    """放り込まれたぶんを1組として持つ。

    `kind` ごとに置き場所が分かれます(変換用 / 集計用)。タブを切り替えても
    持ち越せるよう、**別々に覚えます**。
    """

    def __init__(self, kind: str = CONVERT, suffixes=SUFFIXES) -> None:
        self.kind = kind
        self.suffixes = tuple(suffixes)
        self._lock = threading.Lock()
        self._names: list[str] = []
        #: **写さずに、その場で読む相手**(デスクトップ版)。空ならふだんどおり
        self._paths: list[Path] = []

    @property
    def folder(self) -> Path:
        return folder(self.kind)

    # -- 受け取る ----------------------------------------------------
    def begin(self) -> Path:
        """新しく受け取り始める。**前の組は消します**(混ざらないように)。"""
        self.clear()
        with self._lock:
            self._paths = []
        target = self.folder
        target.mkdir(parents=True, exist_ok=True)
        return target

    def add(self, name: str, data: bytes) -> str:
        """1本受け取る。**上限と拡張子と大きさはここで断ります。**"""
        clean = safe_name(name)
        if Path(clean).suffix.lower() not in self.suffixes:
            raise ValueError(f"{clean}: 読めるのは "
                             + " と ".join(self.suffixes) + " です")
        if clean.startswith("~$"):
            raise ValueError(f"{clean}: Excelが開いている間の作業ファイルです")
        limit = app_config.max_files()
        with self._lock:
            if len(self._names) >= limit:
                raise ValueError(f"一度に受けられるのは {limit} 本までです")
            if len(data) > app_config.max_file_bytes():
                raise ValueError(
                    f"{clean}: 大きすぎます"
                    f"(上限 {app_config.max_file_bytes() // 1024 // 1024}MB)")
            target = self.folder
            target.mkdir(parents=True, exist_ok=True)
            path = target / clean
            stem, suffix = path.stem, path.suffix
            index = 1
            while path.exists():                  # 同じ名前が2つ来ることがある
                path = target / f"{stem}({index}){suffix}"
                index += 1
            path.write_bytes(data)
            self._names.append(path.name)
            return path.name

    def use_paths(self, paths) -> list[Path]:
        """**写さずに、本当の場所をそのまま読む**(デスクトップ版の落とし込み)。

        窓(Tauri)は落とされたファイルの**本当の場所**を教えてくれます。
        写す理由が無いので、ここでは場所だけ覚えます ── 中身を送らないので
        **1本の大きさの上限もありません**。

        フォルダが落とされたら、その中の読める相手を集めます(コマンド版と
        同じ集め方 ── 名前順)。読めない拡張子は**黙って捨てずに**
        `ValueError` で断ります。
        """
        from .run import gather

        found: list[Path] = []
        for one in paths:
            path = Path(str(one)).expanduser()
            if path.is_dir():
                found.extend(p for p in gather([path])
                             if p.suffix.lower() in self.suffixes)
                continue
            if path.suffix.lower() not in self.suffixes:
                raise ValueError(f"{path.name}: 読めるのは "
                                 + " と ".join(self.suffixes) + " です")
            if path.name.startswith("~$"):
                raise ValueError(f"{path.name}: Excelが開いている間の作業ファイルです")
            if not path.is_file():
                raise ValueError(f"{path.name}: 見つかりません")
            found.append(path)

        limit = app_config.max_files()
        if len(found) > limit:
            raise ValueError(f"一度に受けられるのは {limit} 本までです"
                             f"(いまは {len(found)} 本)")
        with self._lock:
            # **写したものと混ぜません。** どちらか一方です
            self._names = []
            self._paths = sorted(found, key=lambda p: p.name)
            picked = list(self._paths)
        try:
            shutil.rmtree(self.folder)            # 前に写したものは要りません
        except (FileNotFoundError, OSError):
            pass
        return picked

    # -- 問い合わせ --------------------------------------------------
    def files(self) -> list[Path]:
        """受け取った順ではなく**名前順**。フォルダ指定と同じ並びにします。"""
        with self._lock:
            if self._paths:
                # **その場で読む相手**(デスクトップ版)。消えていたら外します
                return [p for p in self._paths if p.is_file()]
            target = self.folder
            return sorted((target / n for n in self._names if (target / n).exists()),
                          key=lambda p: p.name)

    @property
    def in_place(self) -> bool:
        """写さずに読む相手を持っているか(画面に「写していません」と出すため)。"""
        with self._lock:
            return bool(self._paths)

    def names(self) -> list[str]:
        return [p.name for p in self.files()]

    def count(self) -> int:
        return len(self.files())

    # -- 片づけ ------------------------------------------------------
    def clear(self) -> None:
        """写したものを消し、覚えていた場所も忘れます。

        **消すのは `work\\intake` に写したものだけ**です ── 落とし込みで
        指された本当のブックには触りません(人のファイルを消しません)。
        """
        with self._lock:
            self._names = []
            self._paths = []
        try:
            shutil.rmtree(self.folder)
        except FileNotFoundError:
            pass
        except OSError as exc:                    # noqa: BLE001 - 続けてよい
            log.warning("受け取り置き場を消せませんでした: %s", exc)


#: 変換タブの受け取り(.xlsx / .xlsm)
INTAKE = Intake(CONVERT, SUFFIXES)

#: 機側データ集計タブの受け取り(.xlsx / .xlsm / .csv)
TALLY_INTAKE = Intake(TALLY, TALLY_SUFFIXES)


def get(kind: str) -> Intake:
    """用途から受け取り置き場を選ぶ。**知らない名前は断ります。**"""
    if kind == TALLY:
        return TALLY_INTAKE
    if kind in ("", CONVERT):
        return INTAKE
    raise ValueError(f"知らない受け取り先です: {kind!r}")


def clear_all() -> None:
    """両方片づける(起動時と終了時に呼びます)。"""
    INTAKE.clear()
    TALLY_INTAKE.clear()
