"""変換(長時間処理。基盤仕様書 3章 レベル2)

進み具合はプロセス側(`converter.jobs`)が持ち、画面はそれを見に行く
だけです。**開き直しても同じものが見えます** ── ブラウザを閉じても
変換は続きます。
"""
from __future__ import annotations

from converter import intake, jobs, user_settings

from . import Blueprint, Request, Response, json_response, refuse

bp = Blueprint("job")


@bp.post("/api/job/start")
def start(request: Request) -> Response:
    payload = request.json()
    if str(payload.get("tab", "convert")) == "tally":
        return _start_tally(payload)

    source = str(payload.get("source", "intake"))
    dry_run = bool(payload.get("dry_run", False))
    tally = bool(payload.get("tally", False))
    settings = user_settings.load()

    found = user_settings.problems(settings, need_input=(source == "folder"))
    if dry_run:
        found = [m for m in found if "出力先" not in m]   # 書かないので要りません
    if found:
        return refuse("設定を確かめてください", "／".join(found), status=400)

    books = (jobs.gather(settings.input_dir) if source == "folder"
             else intake.INTAKE.files())
    try:
        snapshot = jobs.RUNNER.start(books, settings, dry_run=dry_run,
                                     tally=tally)
    except (ValueError, RuntimeError) as exc:
        return refuse("始められません", str(exc), status=400)
    return json_response({"ok": True, "job": snapshot})


def _start_tally(payload: dict) -> Response:
    """機側データ集計タブ。**変換はしません**(4本組CSVは出ません)。"""
    settings = user_settings.load()
    if not settings.output_dir:
        return refuse("設定を確かめてください",
                      "出力先フォルダが未設定です(設定部で指定してください)",
                      status=400)
    sources = intake.get(intake.TALLY).files()
    try:
        snapshot = jobs.RUNNER.start_tally(sources, settings)
    except (ValueError, RuntimeError) as exc:
        return refuse("始められません", str(exc), status=400)
    return json_response({"ok": True, "job": snapshot})


@bp.get("/api/job")
def status(request: Request) -> Response:
    return json_response({"ok": True, "job": jobs.RUNNER.snapshot()})


@bp.post("/api/job/cancel")
def cancel(request: Request) -> Response:
    """中止を頼む。**読み取り中だけ効きます**(書き出しは止めません)。"""
    accepted = jobs.RUNNER.cancel()
    return json_response({"ok": True, "accepted": accepted,
                          "job": jobs.RUNNER.snapshot()})
