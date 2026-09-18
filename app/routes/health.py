"""起動確認・生存監視・停止(基盤仕様書 2.3 / 2.8 / 2.9)

- `GET  /`               … 準備中なら起動待機画面、終わっていればアプリ画面
- `GET  /favicon.ico`    … タブのアイコン
- `GET  /api/health`     … 起動確認と多重起動の判定。**トークン不要**
- `POST /api/alive`      … 画面の心拍。**トークン不要**(切れても落とさない)
- `POST /api/shutdown`   … 安全な停止。トークン必須
- `GET  /api/diagnostics`… 調べるときの1枚
"""
from __future__ import annotations

import os
import sys
import time

from converter import app_config, boot_screen, intake, jobs, screen_lock
from converter.logging_utils import get_logger, log_paths

from . import Blueprint, Request, Response, html_response, json_response, refuse
from .. import shell

log = get_logger("app.health")

bp = Blueprint("health")


@bp.get("/")
def index(request: Request) -> Response:
    """準備が終わるまでは起動待機画面を出す。

    基盤仕様書 2.3 は「ブラウザーだけが先に開き、接続エラーや未完成の
    画面が表示されることを防ぐ」ことを求めています。**サーバ自身が
    待機画面を出す**ことで、起動直後でも必ず何かが表示されます。

    骨格は `converter.boot_screen` に1つだけ ── 起動サーバ
    (`boot_server`)が出すものと同じです。2枚持つと、直したほうと
    直していないほうが場面によって出ます。

    画面そのものは**毎回サーバへ取りに来させます**。キャッシュから
    出されると、2枚目のタブが「開いてよいか」を聞かずに表示されます。
    """
    app = request.app
    if not app.ready:
        return html_response(boot_screen.render(
            display_name=app.config["DISPLAY_NAME"],
            version_label=app_config.version_label(),
            app_id=app.config["APP_ID"],
            poll_ms=app_config.job_poll_ms(),
            home_url="/",
            log_dir=log_paths()["folder"]))
    return html_response(shell.render("index.html", app.config))


@bp.get("/favicon.ico")
def favicon(request: Request) -> Response:
    """タブのアイコン。**ブラウザは data: を埋めても取りに来ることがある**ので、
    ここでも同じものを返します(取りに来るたびに404がログへ残らないように)。
    """
    return Response(body=shell.favicon().encode("utf-8"),
                    content_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@bp.get("/api/health")
def health(request: Request) -> Response:
    """**アプリを識別できる形で**答える。

    同じポートを別のアプリが使っていることがあるので、HTTPが返ったこと
    だけでは「起動した」と判断できません。待機画面と多重起動の判定は、
    ここが返す `app_id` を見て決めます。形は `boot_server.BootApp.health`
    と同じです。
    """
    app = request.app
    job = jobs.RUNNER.job
    return json_response({
        "app_id": app.config["APP_ID"],
        "display_name": app.config["DISPLAY_NAME"],
        "version": app.config["VERSION"],
        "port": app.config["PORT"],
        "pid": os.getpid(),
        "ready": app.ready,
        "stage": app.config["STAGE"],
        "stage_key": app.config["STAGE_KEY"],
        "startup_error": app.config["STARTUP_ERROR"],
        "uptime_sec": round(time.time() - app.config["STARTED_AT"], 1),
        "monitor_level": app_config.monitor_level(),
        "screen": screen_lock.LOCK.status(),
        "job": {"running": jobs.RUNNER.running, "state": job.state},
    }, **{"Cache-Control": "no-store"})


@bp.post("/api/alive")
def alive(request: Request) -> Response:
    """画面の心拍(基盤仕様書 2.9)。**返すのは状態だけ。**

    業務データを載せないので、トークンを要求しません ── 要求すると、
    トークンが切れた画面が黙って死んだ扱いになり、開いているのに
    自動終了してしまいます(`converter.idle_exit`)。

    `closed` が真なら、そのタブが閉じられたという知らせです
    (`sendBeacon`)。猶予のあとで終わります。
    """
    app = request.app
    payload = request.json()
    screen_id = request.header(screen_lock.HEADER)
    if app.idle is not None:
        if payload.get("closed"):
            app.idle.mark_closed()
        else:
            app.idle.mark_alive()
    return json_response({"ok": True,
                          "screen_ok": screen_lock.LOCK.beat(screen_id),
                          "ready": app.ready})


@bp.post("/api/shutdown")
def shutdown(request: Request) -> Response:
    """バックエンドを終わらせる。**実行中は確かめてから。**"""
    app = request.app
    payload = request.json()
    if jobs.RUNNER.running and not payload.get("force"):
        job = jobs.RUNNER.job
        return json_response({
            "ok": False, "running": True, "reason": "変換中です",
            "detail": f"{job.done}/{job.total} 本まで進んでいます。"
                      "中止して終了しますか?",
        }, status=409)
    if jobs.RUNNER.running:
        jobs.RUNNER.cancel()
    who = "画面" if screen_lock.LOCK.holds(request.header(screen_lock.HEADER)) \
        else "stop.bat"
    log.info("停止を受け付けました(%s)", who)
    if app.shutdown_hook is not None:
        app.shutdown_hook(f"{who}から停止を頼まれました")
    return json_response({"ok": True})


@bp.get("/api/diagnostics")
def diagnostics(request: Request) -> Response:
    """調べるときの1枚(基盤仕様書 2.6)。"""
    return json_response({
        "ok": True,
        "text": app_config.describe(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "pid": os.getpid(),
        "logs": log_paths(),
        "local_root": str(app_config.local_root()),
        "config_error": app_config.load_error(),
        "version_problem": app_config.version_problem(),
        "intake": str(intake.folder()),
        "idle": request.app.idle.status() if request.app.idle else {},
    })
