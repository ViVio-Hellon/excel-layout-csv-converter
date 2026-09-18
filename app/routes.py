"""画面とAPIの受付(基盤仕様書 4.3 routes)

ここは**受け取って、確かめて、渡すだけ**です。変換は `jobs`、設定は
`settings_store`、画面の占有は `screen` が持っています。

【断り方の置き場所】
2枚目の画面を断るのはここ1か所です。`/api/screen/claim` 以外のAPIは
**占有している画面の合図(token)が無ければ通しません**。断られた画面から
「変換開始」や「中止」が飛んでも効かない、という状態をここで作ります。

【外からの横取りを受けない】
`127.0.0.1` にしか待ち受けていませんが、利用者が見ている**別のサイト**が
このURLへ書き込みを投げることはできます(返事は読めませんが、効いて
しまう)。変換を勝手に止められては困るので、`Origin` が自分でない書き込み
は受けません。停止だけは画面が無くても要るので、ローカル領域に置いた
合図(`stop_key`)を持っている相手 ── つまり同じ端末の `stop.bat` ── に
限って通します。
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import appinfo, intake, jobs, logging_setup, screen, settings_store

log = logging.getLogger("xlcsv.app")

STATIC_DIR = appinfo.APP_ROOT / "app" / "static"
TEMPLATE_DIR = appinfo.APP_ROOT / "app" / "templates"


@dataclass
class Runtime:
    """サーバが動いているあいだの状態。`server.py` が作ります。"""

    started_at: float = field(default_factory=time.time)
    ready: bool = False
    stage: str = "準備中"
    stop_key: str = ""
    shutdown: threading.Event = field(default_factory=threading.Event)
    stop_reason: str = ""

    def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        self.shutdown.set()


RUNTIME = Runtime()


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)


@dataclass
class Request:
    method: str
    path: str
    query: dict
    body: bytes
    headers: dict

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            found = json.loads(self.body.decode("utf-8"))
            return found if isinstance(found, dict) else {}
        except Exception:                         # noqa: BLE001
            return {}

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    @property
    def token(self) -> str:
        return self.header("x-screen-token")


# ------------------------------------------------------------------
# 返し方
# ------------------------------------------------------------------
def json_response(payload: dict, status: int = 200, **headers) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(status=status, body=body, headers=headers)


def refuse(reason: str, detail: str = "", status: int = 409, **extra) -> Response:
    return json_response({"ok": False, "reason": reason, "detail": detail,
                          **extra}, status=status)


# ------------------------------------------------------------------
# 経路
# ------------------------------------------------------------------
ROUTES: dict[tuple[str, str], Callable[[Request], Response]] = {}


def route(method: str, path: str):
    def register(func):
        ROUTES[(method, path)] = func
        return func
    return register


def _same_origin(request: Request) -> bool:
    origin = request.header("origin")
    if not origin:                                # 画面からの fetch は付けます
        return True                               # (付かない相手は下で弾きます)
    allowed = {f"http://{appinfo.host()}:{appinfo.port()}",
               f"http://localhost:{appinfo.port()}"}
    return origin in allowed


def dispatch(request: Request) -> Response:
    """1つの要求を捌く。**占有と出どころの確認はここでまとめて。**"""
    if request.path.startswith("/static/"):
        return _static(request.path[len("/static/"):])

    handler = ROUTES.get((request.method, request.path))
    if handler is None:
        return refuse("そのURLはありません", request.path, status=404)

    is_api = request.path.startswith("/api/")
    open_paths = {"/api/health", "/api/screen/claim", "/api/shutdown"}
    if request.method != "GET" and not _same_origin(request):
        log.warning("別の出どころからの書き込みを断りました: %s %s",
                    request.path, request.header("origin"))
        return refuse("この操作は受け付けられません",
                      "アプリの画面以外からの操作です", status=403)
    if is_api and request.path not in open_paths:
        # **通った操作は生存確認を兼ねます。** 画面が動いている以上、
        # 見ていないことにはできません(長い変換の最中は問い合わせが
        # 続くので、合図が1回落ちても締め出されません)
        if not screen.LOCK.beat(request.token):
            return refuse(
                "この画面は使えなくなりました",
                "別の画面で開かれたか、しばらく応答がありませんでした。"
                "この画面を閉じて、開き直してください。", status=409,
                screen_lost=True)
    return handler(request)


# ------------------------------------------------------------------
# 画面
# ------------------------------------------------------------------
@route("GET", "/")
def index(request: Request) -> Response:
    """アプリ画面。**毎回サーバへ取りに来させます。**

    キャッシュから出されると、2枚目のタブが「開いてよいか」を聞かずに
    表示されてしまいます。占有の判定はこの画面の中の合図で行うので、
    画面そのものは必ず取りに来る必要があります。
    """
    html = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{APP_NAME}}", appinfo.display_name())
    html = html.replace("{{VERSION}}", appinfo.version_label())
    html = html.replace("{{BOOT}}", json.dumps({
        "app_id": appinfo.app_id(),
        "name": appinfo.display_name(),
        "version": appinfo.version(),
        "max_files": appinfo.max_files(),
        "max_file_mb": appinfo.max_file_bytes() // 1024 // 1024,
        "heartbeat_ms": appinfo.heartbeat_seconds() * 1000,
        "job_poll_ms": appinfo.job_poll_ms(),
        "monitor_level": appinfo.monitor_level(),
    }, ensure_ascii=False))
    return Response(body=html.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store, must-revalidate"})


def _static(relative: str) -> Response:
    target = (STATIC_DIR / relative).resolve()
    if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
        return refuse("そのファイルはありません", relative, status=404)
    kind, _enc = mimetypes.guess_type(target.name)
    return Response(body=target.read_bytes(),
                    content_type=f"{kind or 'application/octet-stream'}; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------
# 起動確認(基盤仕様書 2.3)
# ------------------------------------------------------------------
@route("GET", "/api/health")
def health(request: Request) -> Response:
    """**アプリを識別できる形で**答えます。

    同じポートを別のアプリが使っていることがあるので、HTTPが返ったこと
    だけで「起動した」と判断できません。待機画面と多重起動の判定は、
    ここが返す `app_id` を見て決めます。
    """
    return json_response({
        "app_id": appinfo.app_id(),
        "name": appinfo.display_name(),
        "version": appinfo.version(),
        "ready": RUNTIME.ready,
        "stage": RUNTIME.stage,
        "pid": os.getpid(),
        "port": appinfo.port(),
        "url": appinfo.base_url(),
        "started_at": RUNTIME.started_at,
        "uptime": int(time.time() - RUNTIME.started_at),
        "monitor_level": appinfo.monitor_level(),
        "screen": screen.LOCK.status(),
        "job": {"running": jobs.RUNNER.running,
                "state": jobs.RUNNER.job.state},
    }, **{"Access-Control-Allow-Origin": "*"})     # 待機画面(file://)が読むため


# ------------------------------------------------------------------
# 画面の占有(2枚目を断る)
# ------------------------------------------------------------------
@route("POST", "/api/screen/claim")
def screen_claim(request: Request) -> Response:
    payload = request.json()
    claim = screen.LOCK.claim(str(payload.get("tab_id", "")),
                              str(payload.get("load_id", "")),
                              request.header("user-agent"))
    if not claim.ok:
        log.info("2枚目の画面を断りました: %s", claim.detail)
        return json_response({"ok": False, "reason": claim.reason,
                              "detail": claim.detail,
                              "retry_after": claim.retry_after}, status=409)
    log.info("画面を渡しました tab=%s", str(payload.get("tab_id", ""))[:8])
    return json_response({"ok": True, "token": claim.token})


@route("POST", "/api/screen/beat")
def screen_beat(request: Request) -> Response:
    """生存確認(基盤仕様書 2.9)。**進み具合も一緒に返します。**

    占有の延長は `dispatch` が済ませています(ここへ来た時点で持ち主)。
    画面はこの返事で「繋がっているか」と「いまの進み具合」を同時に知るので、
    問い合わせを2本に分けずに済みます。
    """
    return json_response({"ok": True, "job": jobs.RUNNER.snapshot(),
                          "ready": RUNTIME.ready})


@route("POST", "/api/screen/release")
def screen_release(request: Request) -> Response:
    screen.LOCK.release(request.token)
    return json_response({"ok": True})


# ------------------------------------------------------------------
# 設定部
# ------------------------------------------------------------------
def _settings_payload(settings: settings_store.Settings,
                      message: str = "") -> Response:
    return json_response({
        "ok": True,
        "settings": settings.as_dict(),
        "paths": settings_store.describe_paths(settings),
        "message": message,
        "problems": settings_store.problems(settings, need_input=False),
        "logs": logging_setup.log_paths(),
    })


@route("GET", "/api/settings")
def settings_get(request: Request) -> Response:
    return _settings_payload(settings_store.load())


@route("POST", "/api/settings")
def settings_post(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("変換中は設定を変えられません",
                      "終わるまで待つか、中止してください")
    saved = settings_store.save(request.json())
    settings_store.apply_to_vendor(saved)
    log.info("設定を保存しました: %s", saved.as_dict())
    return _settings_payload(saved, "保存しました")


# ------------------------------------------------------------------
# 入力(フォルダ指定 / ドラッグ投入)
# ------------------------------------------------------------------
@route("POST", "/api/input/scan")
def input_scan(request: Request) -> Response:
    """指定フォルダに何本あるかを先に見せる。**押す前に分かるように。**"""
    settings = settings_store.load()
    folder = str(request.json().get("folder", "") or settings.input_dir)
    if not folder:
        return json_response({"ok": True, "count": 0, "names": [],
                              "message": "入力フォルダが未設定です"})
    try:
        found = jobs.gather(folder)
    except ValueError as exc:
        return json_response({"ok": False, "count": 0, "names": [],
                              "message": str(exc)})
    limit = appinfo.max_files()
    message = f"{len(found)} 本見つかりました"
    if len(found) > limit:
        message = (f"{len(found)} 本見つかりました ── 一度に読めるのは "
                   f"{limit} 本までです。フォルダを分けてください")
    return json_response({"ok": len(found) <= limit, "count": len(found),
                          "over": len(found) > limit, "limit": limit,
                          "names": [p.name for p in found[:limit]],
                          "message": message})


@route("POST", "/api/input/begin")
def input_begin(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("変換中は受け取れません", "終わるまで待ってください")
    intake.INTAKE.begin()
    return json_response({"ok": True, "count": 0, "limit": appinfo.max_files()})


@route("POST", "/api/input/add")
def input_add(request: Request) -> Response:
    """ドラッグで放り込まれた1本。**中身をそのまま body で受けます。**

    (フォルダ指定と違い、ブラウザは場所を教えてくれないので中身が来ます。
     名前は `X-Filename` に URL エンコードで入っています)
    """
    if jobs.RUNNER.running:
        return refuse("変換中は受け取れません", "終わるまで待ってください")
    name = urllib.parse.unquote(request.header("x-filename", ""))
    try:
        saved = intake.INTAKE.add(name, request.body)
    except ValueError as exc:
        return json_response({"ok": False, "message": str(exc),
                              "count": intake.INTAKE.count()}, status=200)
    return json_response({"ok": True, "name": saved,
                          "count": intake.INTAKE.count(),
                          "limit": appinfo.max_files()})


@route("POST", "/api/input/clear")
def input_clear(request: Request) -> Response:
    if jobs.RUNNER.running:
        return refuse("変換中は片づけられません", "終わるまで待ってください")
    intake.INTAKE.clear()
    return json_response({"ok": True, "count": 0})


@route("GET", "/api/input")
def input_list(request: Request) -> Response:
    return json_response({"ok": True, "names": intake.INTAKE.names(),
                          "count": intake.INTAKE.count(),
                          "limit": appinfo.max_files()})


# ------------------------------------------------------------------
# 変換(長時間処理。基盤仕様書 3章 レベル2)
# ------------------------------------------------------------------
@route("POST", "/api/job/start")
def job_start(request: Request) -> Response:
    payload = request.json()
    source = str(payload.get("source", "intake"))
    dry_run = bool(payload.get("dry_run", False))
    settings = settings_store.load()

    found = settings_store.problems(settings, need_input=(source == "folder"))
    if dry_run:
        found = [m for m in found if "出力先" not in m]   # 書かないので要りません
    if found:
        return refuse("設定を確かめてください", "／".join(found), status=400)

    if source == "folder":
        books = jobs.gather(settings.input_dir)
    else:
        books = intake.INTAKE.files()
    try:
        snapshot = jobs.RUNNER.start(books, settings, dry_run=dry_run)
    except (ValueError, RuntimeError) as exc:
        return refuse("始められません", str(exc), status=400)
    return json_response({"ok": True, "job": snapshot})


@route("GET", "/api/job")
def job_status(request: Request) -> Response:
    return json_response({"ok": True, "job": jobs.RUNNER.snapshot()})


@route("POST", "/api/job/cancel")
def job_cancel(request: Request) -> Response:
    stopped = jobs.RUNNER.cancel()
    return json_response({"ok": True, "accepted": stopped,
                          "job": jobs.RUNNER.snapshot()})


# ------------------------------------------------------------------
# 出したものを見に行く(**ダウンロードはしません**)
# ------------------------------------------------------------------
@route("POST", "/api/open")
def open_folder(request: Request) -> Response:
    """出力先やログのフォルダをエクスプローラーで開く。

    開けるのは**このアプリが知っている場所だけ**です。画面から渡された
    道をそのまま開くと、別のサイトに任意の場所を開かせる隙になります。
    """
    what = str(request.json().get("what", "out"))
    settings = settings_store.load()
    try:
        if what == "out":
            target = settings_store.resolve(settings.output_dir)
        elif what == "input":
            target = settings_store.resolve(settings.input_dir)
        elif what == "logs":
            target = appinfo.local_dir("logs")
        else:
            return refuse("開けない場所です", what, status=400)
    except ValueError as exc:
        return refuse("場所が決まっていません", str(exc), status=400)

    if not target.is_dir():
        return refuse("まだありません", str(target), status=400)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))             # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:                      # noqa: BLE001
        return refuse("開けませんでした", str(exc), status=500)
    return json_response({"ok": True, "opened": str(target)})


# ------------------------------------------------------------------
# 停止(基盤仕様書 2.8)
# ------------------------------------------------------------------
@route("POST", "/api/shutdown")
def shutdown(request: Request) -> Response:
    """バックエンドを終わらせる。**実行中は確かめてから。**"""
    payload = request.json()
    key = str(payload.get("stop_key", "")) or request.header("x-stop-key")
    by_screen = screen.LOCK.holds(request.token)
    if not by_screen and not (RUNTIME.stop_key and key == RUNTIME.stop_key):
        return refuse("停止できません", "この操作の出どころを確かめられません",
                      status=403)
    if jobs.RUNNER.running and not payload.get("force"):
        job = jobs.RUNNER.job
        return json_response({
            "ok": False, "running": True,
            "reason": "変換中です",
            "detail": f"{job.done}/{job.total} 本まで進んでいます。"
                      "中止して終了しますか?",
        }, status=409)
    if jobs.RUNNER.running:
        jobs.RUNNER.cancel()
    who = "画面" if by_screen else "stop.bat"
    log.info("停止を受け付けました(%s)", who)
    RUNTIME.request_stop(f"{who}から停止を頼まれました")
    return json_response({"ok": True})


@route("GET", "/api/diagnostics")
def diagnostics(request: Request) -> Response:
    """調べるときの1枚(基盤仕様書 2.6)。"""
    return json_response({
        "ok": True,
        "text": appinfo.describe(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "pid": os.getpid(),
        "logs": logging_setup.log_paths(),
        "local_root": str(appinfo.local_root()),
        "config_error": appinfo.load_error(),
        "version_problem": appinfo.version_problem(),
        "intake": str(intake.folder()),
    })
