r"""読ませるブックを受ける ── ドラッグ投入とフォルダ指定

【なぜ中身を送ってもらうのか】
ブラウザは、放り込まれたファイルの**本当の場所(フルパス)を教えません**。
安全のための決まりで、社内アプリでも変えられません。渡されるのは中身
だけなので、いったんこの端末のローカル領域へ写してから、フォルダを
指定したときとまったく同じ道で読みます(`converter.intake`)。

**出したCSVをダウンロードさせることはありません。** 受け取る向きだけです。

【置き場所はタブごと】
変換タブと機側データ集計タブで**別々に覚えます** ── 片方を片づけたときに
もう片方まで消えると、「入れたはずのものが無い」になります。
"""
from __future__ import annotations

import urllib.parse

from converter import app_config, intake, jobs, user_settings
from converter.logging_utils import get_logger

from . import Blueprint, Request, Response, json_response, refuse

log = get_logger("app.input")

bp = Blueprint("input")


def _intake(request: Request):
    """どちらの受け取り置き場か。**知らない名前なら `None`。**

    変換タブ(`convert`)は .xlsx / .xlsm、機側データ集計タブ(`tally`)は
    CSVも受けます。名前は本文・クエリ・ヘッダのどれで来ても拾います
    (中身をそのまま送る `add` は本文を使えないため)。
    """
    kind = (str(request.json().get("kind", "")) or request.arg("kind")
            or request.header("x-intake") or intake.CONVERT)
    try:
        return intake.get(kind)
    except ValueError:
        return None


def _unknown(request: Request) -> Response:
    return refuse("知らない受け取り先です",
                  f"使えるのは {intake.CONVERT} と {intake.TALLY} です",
                  status=400)


@bp.get("/api/input")
def listing(request: Request) -> Response:
    store = _intake(request)
    if store is None:
        return _unknown(request)
    return json_response({"ok": True, "kind": store.kind,
                          "names": store.names(), "count": store.count(),
                          "suffixes": list(store.suffixes),
                          "limit": app_config.max_files()})


@bp.post("/api/input/scan")
def scan(request: Request) -> Response:
    """指定フォルダに何本あるかを先に見せる。**押す前に分かるように。**"""
    settings = user_settings.load()
    folder = str(request.json().get("folder", "") or settings.input_dir)
    if not folder:
        return json_response({"ok": True, "count": 0, "names": [],
                              "message": "入力フォルダが未設定です"})
    try:
        found = jobs.gather(folder)
    except ValueError as exc:
        return json_response({"ok": False, "count": 0, "names": [],
                              "message": str(exc)})
    limit = app_config.max_files()
    message = f"{len(found)} 本見つかりました"
    if len(found) > limit:
        message = (f"{len(found)} 本見つかりました ── 一度に読めるのは "
                   f"{limit} 本までです。フォルダを分けてください")
    return json_response({"ok": len(found) <= limit, "count": len(found),
                          "over": len(found) > limit, "limit": limit,
                          "names": [p.name for p in found[:limit]],
                          "message": message})


@bp.post("/api/input/begin")
def begin(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("実行中は受け取れません", "終わるまで待ってください")
    store = _intake(request)
    if store is None:
        return _unknown(request)
    store.begin()
    return json_response({"ok": True, "kind": store.kind, "count": 0,
                          "limit": app_config.max_files()})


@bp.post("/api/input/add")
def add(request: Request) -> Response:
    """放り込まれた1本。**中身をそのまま body で受けます。**

    名前は `X-Filename` に URL エンコードで入っています。
    上限・拡張子・大きさは `converter.intake` が断ります。
    """
    if jobs.RUNNER.running:
        return refuse("実行中は受け取れません", "終わるまで待ってください")
    store = _intake(request)
    if store is None:
        return _unknown(request)
    name = urllib.parse.unquote(request.header("x-filename", ""))
    try:
        saved = store.add(name, request.body)
    except ValueError as exc:
        return json_response({"ok": False, "message": str(exc),
                              "kind": store.kind, "count": store.count()})
    return json_response({"ok": True, "name": saved, "kind": store.kind,
                          "count": store.count(),
                          "limit": app_config.max_files()})


@bp.post("/api/input/dropped")
def dropped(request: Request) -> Response:
    """**落とされたファイルの本当の場所**を受け取る(デスクトップ版だけ)。

    ブラウザは本当の場所を教えてくれないので、中身をコピーして送って
    もらっていました(1本 50MB まで)。**窓(Tauri)は場所を教えてくれます** ──
    写さずにその場で読むので、コピーも大きさの上限もありません。

    フォルダを落とされたら、その中の読める相手を集めます。
    """
    if jobs.RUNNER.running:
        return refuse("実行中は受け取れません", "終わるまで待ってください")
    store = _intake(request)
    if store is None:
        return _unknown(request)
    paths = request.json().get("paths") or []
    if not isinstance(paths, list) or not paths:
        return json_response({"ok": False, "message": "場所が空です",
                              "kind": store.kind, "count": store.count()})
    try:
        found = store.use_paths(paths)
    except ValueError as exc:
        return json_response({"ok": False, "message": str(exc),
                              "kind": store.kind, "count": store.count()})
    log.info("落とし込み(写さずに読みます): %d 本", len(found))
    return json_response({"ok": True, "kind": store.kind, "count": len(found),
                          "names": [p.name for p in found],
                          "in_place": True,
                          "limit": app_config.max_files()})


@bp.post("/api/input/clear")
def clear(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("実行中は片づけられません", "終わるまで待ってください")
    store = _intake(request)
    if store is None:
        return _unknown(request)
    store.clear()
    return json_response({"ok": True, "kind": store.kind, "count": 0})
