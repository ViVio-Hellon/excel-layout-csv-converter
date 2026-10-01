"""設定部 ── 入力元・出力先・ライン・参照用マスタ・停止内訳CSV

値の持ち方と確かめ方は `converter.user_settings` にあります。ここは
受けて渡すだけです。
"""
from __future__ import annotations

import os
import subprocess
import sys

from converter import app_config, jobs, stop_reasons, user_settings
from converter.logging_utils import get_logger, log_paths

from . import Blueprint, Request, Response, json_response, refuse

log = get_logger("app.settings")

bp = Blueprint("settings")


def _payload(settings: user_settings.Settings, message: str = "") -> Response:
    return json_response({
        "ok": True,
        "settings": settings.as_dict(),
        "paths": user_settings.describe_paths(settings),
        "message": message,
        "problems": user_settings.problems(settings, need_input=False),
        # **停止内訳がどこから読めたか**も返します ── 出どころが2つ
        # (CSVとマスタ)あるので、言わないと「内訳が空」の理由が分からない
        "stop_reasons": _stop_reasons(),
        "logs": log_paths(),
    })


def _stop_reasons() -> dict:
    """停止内訳の読めぐあい。**読めなくても画面は出します。**"""
    try:
        return stop_reasons.read().as_dict()
    except Exception as exc:                       # noqa: BLE001 - 画面は出す
        log.warning("停止内訳を見られませんでした: %s", exc)
        return {}


@bp.get("/api/settings")
def read(request: Request) -> Response:
    return _payload(user_settings.load())


@bp.post("/api/settings")
def write(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("変換中は設定を変えられません",
                      "終わるまで待つか、中止してください")
    saved = user_settings.save(request.json())
    user_settings.apply_to_vendor(saved)
    log.info("設定を保存しました: %s", saved.as_dict())
    return _payload(saved, "保存しました")


@bp.post("/api/open")
def open_folder(request: Request) -> Response:
    """出力先やログのフォルダをエクスプローラーで開く。

    開けるのは**このアプリが知っている場所だけ**です。画面から渡された
    道をそのまま開くと、任意の場所を開かせる隙になります。

    (**ダウンロードはしません。** 出したCSVは設定した出力先へこの端末の
     Pythonが書くので、見に行く道だけを用意します)
    """
    what = str(request.json().get("what", "out"))
    settings = user_settings.load()
    try:
        if what == "out":
            target = user_settings.resolve(settings.output_dir)
        elif what == "input":
            target = user_settings.resolve(settings.input_dir)
        elif what == "logs":
            from converter import logging_utils
            target = logging_utils.folder()       # 設定で変えた先を開きます
        else:
            return refuse("開けない場所です", what, status=400)
    except ValueError as exc:
        return refuse("場所が決まっていません", str(exc), status=400)

    if not target.is_dir():
        return refuse("まだありません", str(target), status=400)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))              # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:                       # noqa: BLE001
        return refuse("開けませんでした", str(exc), status=500)
    return json_response({"ok": True, "opened": str(target)})
