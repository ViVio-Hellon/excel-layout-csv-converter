r"""読ませるブックを受ける ── ドラッグ投入とフォルダ指定

【なぜ中身を送ってもらうのか】
ブラウザは、放り込まれたファイルの**本当の場所(フルパス)を教えません**。
安全のための決まりで、社内アプリでも変えられません。渡されるのは中身
だけなので、いったんこの端末のローカル領域へ写してから、フォルダを
指定したときとまったく同じ道で読みます(`converter.intake`)。

**出したCSVをダウンロードさせることはありません。** 受け取る向きだけです。
"""
from __future__ import annotations

import urllib.parse

from converter import app_config, intake, jobs, user_settings

from . import Blueprint, Request, Response, json_response, refuse

bp = Blueprint("input")


@bp.get("/api/input")
def listing(request: Request) -> Response:
    return json_response({"ok": True, "names": intake.INTAKE.names(),
                          "count": intake.INTAKE.count(),
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
        return refuse("変換中は受け取れません", "終わるまで待ってください")
    intake.INTAKE.begin()
    return json_response({"ok": True, "count": 0, "limit": app_config.max_files()})


@bp.post("/api/input/add")
def add(request: Request) -> Response:
    """放り込まれた1本。**中身をそのまま body で受けます。**

    名前は `X-Filename` に URL エンコードで入っています。
    上限・拡張子・大きさは `converter.intake` が断ります。
    """
    if jobs.RUNNER.running:
        return refuse("変換中は受け取れません", "終わるまで待ってください")
    name = urllib.parse.unquote(request.header("x-filename", ""))
    try:
        saved = intake.INTAKE.add(name, request.body)
    except ValueError as exc:
        return json_response({"ok": False, "message": str(exc),
                              "count": intake.INTAKE.count()})
    return json_response({"ok": True, "name": saved,
                          "count": intake.INTAKE.count(),
                          "limit": app_config.max_files()})


@bp.post("/api/input/clear")
def clear(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("変換中は片づけられません", "終わるまで待ってください")
    intake.INTAKE.clear()
    return json_response({"ok": True, "count": 0})
