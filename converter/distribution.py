r"""配布設定 ── 1台で決めた設定を、配った先の端末でそのまま使う

【なぜ要るのか】
設定(入力元・出力先・参照用マスタ・停止内訳CSV・板データ参照パス・
ログの置き場所)は**端末ごとのローカル領域**に入ります
(`%LOCALAPPDATA%\ExcelCsvConverter\data\settings.json`)。
**ツールのフォルダをコピーしても付いて行きません。** 配った先で1台ずつ
打ち直すのは手間で、打ち間違えると**別の場所を読み書きする端末**が
できます ── 出力先が空のまま変換を始めれば断られますが、参照パスが
1字違う端末は「集計が出ない」理由が分からないまま使われます。

【流れ】(日報管理ツール `nippou/distribution.py` と同じ考え方)
    1. 1台で起動して設定し、設定タブの「配布設定を書き出す」を押す
    2. **起動用の Start.vbs / ExcelCsvConverter.exe と同じフォルダ**に
       `配布設定\` ができ、配るものがそこに入る
    3. フォルダごと配る
    4. 配った先は起動したときに `配布設定\` を見つけて読み込む

    配布設定\
      設定.json          配る値(選んだ項目だけ)
      はじめに読む.txt   何が入っているか・配った先で何が起きるか

【読み込むときの決まり】**その端末にすでにあるものは読み込みません。**
値が入っている項目はそのまま。空の項目だけ埋めます(空の値は「無い」と
数えます ── 「既定に戻す」で空になった項目を既存と数えると、配ったのに
前のまま動く端末ができます)。

【配り直したとき】配布設定から読み込んだ値は**その端末で決めた値では
ありません。** 読み込んだ値を覚えておき(`data\distribution.json`)、
端末の値がそのままなら、配り直した新しい値で置き換えます ──
読み込んだあとに端末で直した値は残します。

**狙って揃えたいときは「読み込み直す」**。端末で決めた値も上書きします。

【パスワードは聞きません】
このツールには管理者パスワードがありません(日報管理ツールにはあります)。
書き出すのは**この端末の設定を書き出すだけ**で、読めるのは配る人自身の
端末の値です ── 守るものが増えないので、鍵を増やしません。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import app_config, user_settings
from .logging_utils import get_logger

log = get_logger("distribution")

#: 配るフォルダとファイルの名前。**起動用のファイルと同じ階層**に作ります
DIR_NAME = "配布設定"
SETTINGS_NAME = "設定.json"
README_NAME = "はじめに読む.txt"

#: 配れる項目。**ライン以外は「どこを読んで、どこへ出すか」**です。
#: ログの置き場所も配ります ── 共有へ集める運用のとき、1台ずつ
#: 打つと必ず打ち漏れが出ます
ITEMS: tuple[str, ...] = ("input_dir", "output_dir", "ref_dir", "stop_csv",
                          "plate_dir", "report_db", "log_dir", "line")

#: 既定で選ぶ項目。**ラインは端末ごとに違う**ので外します
#: (同じラインの端末へ配るときは、画面で選べば入ります)
DEFAULT_ITEMS: tuple[str, ...] = ("output_dir", "ref_dir", "stop_csv",
                                  "plate_dir", "report_db", "log_dir")

#: 書き出した形の版。読むときに見ます(違う版は読みません)
FORMAT = 1

_lock = threading.Lock()


def launch_dir() -> Path:
    r"""起動用のファイルがあるフォルダ(`Start.vbs` と同じ階層)。

    アプリ本体の場所です ── 共有フォルダに置かれることもありますが、
    **配るのはフォルダごと**なので、ここに置くのが配る単位に合います。

    `XLCSV_LAUNCH_DIR` で差し替えられます。**試験が本物のフォルダへ
    `配布設定\` を作らない**ためで(`XLCSV_LOCAL_DIR` と同じ考え方)、
    起動用のファイルを別の場所へ置いた端末でも使えます。
    """
    override = os.environ.get("XLCSV_LAUNCH_DIR")
    return Path(override) if override else app_config.APP_ROOT


def folder() -> Path:
    return launch_dir() / DIR_NAME


def settings_path() -> Path:
    return folder() / SETTINGS_NAME


def applied_path() -> Path:
    """配布設定から読み込んだ値の控え(端末のローカル領域)。"""
    return app_config.local_dir("data") / "distribution.json"


# ----------------------------------------------------------------------
# 読む
# ----------------------------------------------------------------------
def _read_json(path: Path) -> tuple[dict, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError) as exc:
        return {}, f"読めません({exc})"
    if not isinstance(raw, dict):
        return {}, "形が違います"
    return raw, ""


def bundle() -> tuple[dict[str, str], str]:
    """配布設定の値。`(値, 困っていること)`。

    **読めなくても起動は止めません。** 配布設定が壊れていても、端末の
    設定で動けます ── 代わりに理由をログと設定タブに出します。
    """
    raw, problem = _read_json(settings_path())
    if problem:
        return {}, problem
    if not raw:
        return {}, ""
    if int(raw.get("format") or 0) != FORMAT:
        return {}, f"版が違います(この版は {FORMAT} です)"
    values = raw.get("settings")
    if not isinstance(values, dict):
        return {}, "設定が入っていません"
    return {key: user_settings.clean(str(values.get(key) or ""))
            for key in ITEMS if str(values.get(key) or "").strip()}, ""


def _applied() -> dict[str, str]:
    raw, _problem = _read_json(applied_path())
    values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    return {k: str(v or "") for k, v in values.items() if k in ITEMS}


def _remember(values: dict[str, str]) -> None:
    """読み込んだ値を覚える。**配り直したときに置き換えるため。**"""
    app_config.ensure_local_dirs()
    with _lock:
        keep = _applied()
        keep.update(values)
        target = applied_path()
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"values": keep}, ensure_ascii=False,
                                  indent=2), encoding="utf-8")
        tmp.replace(target)


# ----------------------------------------------------------------------
# 起動のときに読み込む
# ----------------------------------------------------------------------
def apply_on_start() -> list[str]:
    """起動のときに1回。**埋めた項目の名前**を返します。

    埋めるのは次の2つだけです。

        その端末で**まだ決まっていない**項目(空)
        前に配布設定から読み込んだまま、端末で直していない項目

    どちらでもない項目(端末で打った値)には触りません。
    """
    values, problem = bundle()
    if problem:
        log.warning("配布設定を読めませんでした: %s (%s)", settings_path(), problem)
        return []
    if not values:
        return []
    settings = user_settings.load()
    now = settings.as_dict()
    came_from = _applied()
    changes: dict[str, str] = {}
    for key, value in values.items():
        current = (now.get(key) or "").strip()
        if not current:
            changes[key] = value                  # まだ決まっていない
        elif came_from.get(key, None) == current and current != value:
            changes[key] = value                  # 前に配ったまま → 置き換える
    if not changes:
        return []
    saved = user_settings.save(changes)
    _remember(changes)
    log.info("配布設定から読み込みました: %s", "／".join(
        f"{user_settings.LABELS.get(k, k)}={v}" for k, v in changes.items()))
    del saved                                     # 渡すのは `boot()` の仕事
    return list(changes)


def boot() -> list[str]:
    """起動のときに1回だけ呼ぶ(`start_app.py` / `bridge.py`)。

    **順番に意味があります。** 配布設定を先に読み込んでから、写してある
    日報管理ツール側へ渡します ── 逆にすると、配った参照用マスタの
    置き場所が効くのが次の起動からになります。
    """
    applied = apply_on_start()
    user_settings.apply_to_vendor(user_settings.load())
    return applied


def reload_now() -> dict:
    """「読み込み直す」。**端末で決めた値も上書き**して揃えます。"""
    values, problem = bundle()
    if problem:
        return {"ok": False, "message": f"配布設定を{problem}"}
    if not values:
        return {"ok": False, "message": "配布設定がありません"}
    saved = user_settings.save(values)
    _remember(values)
    user_settings.apply_to_vendor(saved)
    log.info("配布設定を読み込み直しました: %s", "／".join(values))
    return {"ok": True, "applied": list(values),
            "message": f"{len(values)} 項目を配布設定の値にしました"}


# ----------------------------------------------------------------------
# 書き出す・消す
# ----------------------------------------------------------------------
README = """このフォルダは「配布設定」です({tool} {version})。

何が入っているか
  設定.json   配る設定の値(下に一覧)

配った先で何が起きるか
  起動したときにこのフォルダを読み、**その端末でまだ決まっていない項目
  だけ**を埋めます。端末で打った値は上書きしません。
  揃えたいときは、設定タブの「配布設定を読み込み直す」を押してください。

配り方
  このフォルダを含めて、ツールのフォルダをまるごとコピーしてください。
  (Start.vbs / ExcelCsvConverter.exe と同じ階層にこのフォルダがあること)

入っている項目
{items}
"""


def export(keys: list[str] | None = None) -> dict:
    """この端末の設定を `配布設定\\` へ書き出す。

    **空の項目は書きません。** 空を配ると、配った先の「まだ決まって
    いない」と区別が付かなくなります(埋める相手が無い)。
    """
    settings = user_settings.load().as_dict()
    chosen = [k for k in (keys if keys is not None else DEFAULT_ITEMS)
              if k in ITEMS]
    values = {k: settings.get(k, "") for k in chosen
              if (settings.get(k) or "").strip()}
    if not values:
        return {"ok": False,
                "message": "配る値がありません(先に設定を保存してください)"}
    target = folder()
    try:
        target.mkdir(parents=True, exist_ok=True)
        payload = {"format": FORMAT, "tool": app_config.app_id(),
                   "version": app_config.version(), "settings": values}
        (target / SETTINGS_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        lines = "\n".join(f"  {user_settings.LABELS.get(k, k)}: {v}"
                          for k, v in values.items())
        (target / README_NAME).write_text(
            README.format(tool=app_config.display_name(),
                          version=app_config.version(), items=lines),
            encoding="utf-8")
    except OSError as exc:
        log.warning("配布設定を書けませんでした: %s", exc)
        return {"ok": False, "message": f"書けませんでした({exc})"}
    log.info("配布設定を書き出しました: %s (%d 項目)", target, len(values))
    return {"ok": True, "path": str(target), "applied": list(values),
            "message": f"{target} に {len(values)} 項目を書き出しました"}


def forget() -> dict:
    """配布設定を消す(**この端末の設定はそのまま**)。"""
    target = folder()
    if not target.is_dir():
        return {"ok": False, "message": "配布設定がありません"}
    removed = []
    try:
        for name in (SETTINGS_NAME, README_NAME):
            path = target / name
            if path.is_file():
                path.unlink()
                removed.append(name)
        # **こちらが作ったものだけ**消します。ほかの物が入っていれば
        # フォルダは残します(人が置いたものを消しません)
        if not any(target.iterdir()):
            target.rmdir()
    except OSError as exc:
        return {"ok": False, "message": f"消せませんでした({exc})"}
    log.info("配布設定を消しました: %s", target)
    return {"ok": True, "removed": removed, "message": "配布設定を消しました"}


# ----------------------------------------------------------------------
# 画面へ
# ----------------------------------------------------------------------
def state() -> dict:
    """設定タブに出すもの。**どの値が配布設定から来たのか**まで。"""
    values, problem = bundle()
    came_from = _applied()
    settings = user_settings.load().as_dict()
    return {
        "dir": str(folder()),
        "exists": settings_path().is_file(),
        "problem": problem,
        "values": values,
        # いま画面に出ている値が配布設定から来たものか(札を出すため)
        "from_distribution": [k for k, v in came_from.items()
                              if v and v == (settings.get(k) or "")],
        "items": [{"key": k, "label": user_settings.LABELS.get(k, k),
                   "value": settings.get(k, ""),
                   "default": k in DEFAULT_ITEMS} for k in ITEMS],
    }
