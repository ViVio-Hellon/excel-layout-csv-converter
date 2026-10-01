r"""設定部 ── 入力元・出力先・ライン・参照用マスタ・停止内訳CSV

【`app_config.py` との違い】
- `app_config`     … 配るときに決まる値(アプリID・ポート・上限・ローカル領域)
- `user_settings`  … **使う人が画面で変える値**(どこを読んで、どこへ出すか)

置き場所は利用者ごとのローカル領域の `data`(基盤仕様書 2.7)。アプリ本体は
共有フォルダに置かれることがあるので、そこへ書くと**他の人の設定と
奪い合い**になります。`data` に置くのは、ログやキャッシュと違って
**消してはいけない**ものだからです(4.6)。

【打たれた道の受け方】
エクスプローラーの「パスのコピー」は `"C:\…"` と**引用符つき**で入ります。
そのまま `Path` に渡すと存在しない場所を指すので、ここで外します。
`~` も開きます(共有へ届かない端末で手元の写しを指すため)。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from . import app_config

#: 画面から変えられる項目。**表示名の出どころもここ1つ**
FIELDS = ("input_dir", "output_dir", "line", "ref_dir", "stop_csv",
          "log_dir")

LABELS = {
    "input_dir": "入力フォルダ",
    "output_dir": "出力先フォルダ",
    "line": "ライン",
    "ref_dir": "参照用マスタの置き場所",
    "stop_csv": "停止内訳CSVの置き場所",
    "log_dir": "ログの置き場所",
}

#: 停止内訳CSVの名前。**フォルダを指されたときに探す相手**
#: (移行元の既定と同じ名前 ── `nippou.config` の `stop_reason_csv_filename`)
STOP_CSV_NAME = "停止内訳.csv"

_lock = threading.Lock()


def path_of() -> Path:
    return app_config.local_dir("data") / "settings.json"


@dataclass
class Settings:
    input_dir: str = ""
    output_dir: str = ""
    line: str = ""
    ref_dir: str = ""
    #: 停止内訳CSV。**フォルダでも、ファイルそのものでも受けます**
    #: (空なら参照用マスタの置き場所の `停止内訳.csv`)
    stop_csv: str = ""
    #: ログの置き場所(空ならローカル領域の `logs`)。**書けなければ戻します**
    log_dir: str = ""

    def as_dict(self) -> dict[str, str]:
        return {k: str(v or "") for k, v in asdict(self).items()}


def clean(text: str) -> str:
    """打たれた道を、**実際に見に行く形**にする(引用符と前後の空白を外す)。"""
    return (text or "").strip().strip('"').strip()


def resolve(text: str) -> Path:
    """`~` を開いた `Path`。相対は**アプリ本体のフォルダから**たどります。

    「いまの作業フォルダから」にしないのが要点です ── 作業フォルダは
    どこから起動したかで変わるので、同じ設定でも端末ごとに違う場所を
    指すことになります。
    """
    trimmed = clean(text)
    if not trimmed:
        raise ValueError("道が空です")
    path = Path(trimmed).expanduser()
    return path if path.is_absolute() else app_config.APP_ROOT / path


def load() -> Settings:
    """保存された設定。無ければ `config/app.json` の既定値。"""
    values = dict(app_config.defaults())
    try:
        raw = json.loads(path_of().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            values.update({k: str(raw.get(k, values.get(k, "")) or "")
                           for k in FIELDS})
    except FileNotFoundError:
        pass
    except Exception:                             # noqa: BLE001 - 既定で続ける
        pass
    return Settings(**{k: clean(values.get(k, "")) for k in FIELDS})


def save(values: dict) -> Settings:
    """設定を書く。**書けたものを返します**(画面はこれを表示し直します)。"""
    current = load()
    merged = current.as_dict()
    for key in FIELDS:
        if key in values:
            merged[key] = clean(str(values.get(key) or ""))
    settings = Settings(**merged)
    app_config.ensure_local_dirs()
    with _lock:
        target = path_of()
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings.as_dict(), ensure_ascii=False,
                                  indent=2), encoding="utf-8")
        tmp.replace(target)                        # **途中で壊れた設定を残さない**
    return settings


# ------------------------------------------------------------------
# 使う前の確かめ ── 「出たはずなのに見当たらない」を作らない
# ------------------------------------------------------------------
def stop_csv_path(settings: Settings) -> Path | None:
    """停止内訳CSVの道。**フォルダを指されたらその中の `停止内訳.csv`。**

    空なら `None` ── 写してある日報管理ツール側の既定(参照用マスタの
    置き場所にある `停止内訳.csv`)にまかせます。
    """
    if not settings.stop_csv:
        return None
    try:
        path = resolve(settings.stop_csv)
    except ValueError:
        return None
    # **まだ無いファイルも受けます**(これから置く場合)。拡張子が無ければ
    # フォルダとみなして、その中の既定の名前を指します
    if path.is_dir() or not path.suffix:
        return path / STOP_CSV_NAME
    return path


def describe_paths(settings: Settings) -> list[dict]:
    """1項目ずつ、**どこを指していて、そこはあるのか**を返す。"""
    found: list[dict] = []
    for key in ("input_dir", "output_dir", "ref_dir", "log_dir"):
        text = getattr(settings, key)
        item = {"key": key, "label": LABELS[key], "text": text,
                "resolved": "", "exists": False, "note": ""}
        if text:
            try:
                path = resolve(text)
                item["resolved"] = str(path)
                item["exists"] = path.is_dir()
                if not item["exists"]:
                    item["note"] = ("まだありません"
                                    if key in ("output_dir", "log_dir")
                                    else "見つかりません")
            except ValueError as exc:
                item["note"] = str(exc)
        found.append(item)

    # 停止内訳CSVは**フォルダではなくファイル**を指します(フォルダを
    # 指されたら中の `停止内訳.csv`)。あるかどうかもファイルで見ます
    item = {"key": "stop_csv", "label": LABELS["stop_csv"],
            "text": settings.stop_csv, "resolved": "", "exists": False,
            "note": ""}
    if settings.stop_csv:
        path = stop_csv_path(settings)
        if path is None:
            item["note"] = "書き方が読めません"
        else:
            item["resolved"] = str(path)
            item["exists"] = path.is_file()
            if not item["exists"]:
                item["note"] = "まだありません(参照用マスタの表を読みます)"
    found.append(item)
    return found


def problems(settings: Settings, *, need_input: bool) -> list[str]:
    """変換を始めてよいか。**空なら始められます。**"""
    found: list[str] = []
    if not settings.output_dir:
        found.append("出力先フォルダが未設定です(設定部で指定してください)")
    else:
        try:
            resolve(settings.output_dir)
        except ValueError:
            found.append("出力先フォルダの書き方が読めません")
    if need_input:
        if not settings.input_dir:
            found.append("入力フォルダが未設定です")
        elif not resolve(settings.input_dir).is_dir():
            found.append(f"入力フォルダが見つかりません: {settings.input_dir}")
    return found


def ensure_output(settings: Settings) -> Path:
    """出力先を用意する。**無ければ作ります**(作れなければ理由を投げます)。"""
    path = resolve(settings.output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------
# 写してある日報管理ツール側へ渡す
# ------------------------------------------------------------------
def apply_to_vendor(settings: Settings) -> None:
    r"""参照用マスタの置き場所を `nippou` 側へ渡す。

    向こうは**読むたびに環境変数を見に行く** property なので、起動後に
    変えてもすぐ効きます(`nippou/config.py` の `gw_reference_dir`)。

    `NIPPOU_APP_DIR` もこちらの領域へ向けます。向こうは既定で
    `%LOCALAPPDATA%\NippouTool\runtime\user_config.json` を先に見るので、
    同じ端末に日報管理ツールが入っていると、**画面で指したはずの場所が
    そちらの設定に上書きされます**。指した場所がそのまま効くように、
    ここで切り離します(変換用の使い捨てDBは `converter.run` が別に作る
    ので、この向き先が業務データに触ることはありません)。
    """
    app_config.ensure_local_dirs()
    # **ログの置き場所を先に決めます。** あとの行で何かあったとき、
    # その知らせが指された場所へ出るように
    from . import logging_utils
    logging_utils.use_folder(settings.log_dir)
    os.environ["NIPPOU_APP_DIR"] = str(app_config.local_dir("work") / "nippou")
    Path(os.environ["NIPPOU_APP_DIR"]).mkdir(parents=True, exist_ok=True)
    if settings.ref_dir:
        try:
            os.environ["NIPPOU_GW_REF_DIR"] = str(resolve(settings.ref_dir))
        except ValueError:
            os.environ.pop("NIPPOU_GW_REF_DIR", None)
    else:
        os.environ.pop("NIPPOU_GW_REF_DIR", None)
    _apply_stop_csv(settings)


def _apply_stop_csv(settings: Settings) -> None:
    """停止内訳CSVの置き場所を `nippou` 側へ渡す。

    向こうは**この端末の設定ファイル**から読みます(`user_settings.get`
    の `stop_reason_file`)。置き場所は上で `NIPPOU_APP_DIR` をこちらの
    領域へ向けてあるので、書くのはこのアプリの持ちものです ── 同じ端末に
    日報管理ツールが入っていても、あちらの設定には触りません。

    **空にしたら消します。** 残しておくと「設定を空にしたのに前の場所を
    読み続ける」になります(向こうは空文字を「既定に戻した」と読むので
    上書きでも足りますが、消すほうが残骸が出ません)。
    """
    try:
        from nippou import user_settings as theirs
    except Exception:                             # noqa: BLE001 - 写しが無い
        return
    key = "stop_reason_file"                      # nippou.config.KEY_STOP_REASON_FILE
    path = stop_csv_path(settings)
    want = str(path) if path else ""
    try:
        now = theirs.get(key)
        now = now.strip() if isinstance(now, str) else ""
        if want == now:
            return
        if want:
            theirs.save(key, want)
        else:
            theirs.forget(key)
    except Exception:                             # noqa: BLE001 - 既定で続ける
        pass
