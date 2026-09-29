"""アプリの組み立て(基盤仕様書 2.5「起動処理とアプリ本体の分離」)

ここは**画面層の入口**です。業務は `converter/` が持ち、待ち受けは
`server.py` が持ちます。この層がするのは、要求を確かめて経路へ渡すこと
だけです。

【守り ── 4つ】
1. **Host の確認** 外のドメインを `127.0.0.1` に向けられても、Hostが
   一致しないので弾けます(DNSリバインディング)
2. **同一オリジンの確認** ブラウザが付ける `Sec-Fetch-Site` を見ます。
   付かない相手(curl・古い環境)は素通しし、次のトークンで守ります
3. **起動トークン** 起動ごとの合言葉。利用者が偶然開いた**別のサイト**
   や、同じPCの別プロセスから操作されないようにします。画面は
   `base.html` 経由で受け取ります
4. **画面の占有** 使ってよい画面は1枚だけ(`converter.screen_lock`)。
   断られた画面から「変換開始」や「中止」が飛んでも効きません

【免除するもの】
`/api/health` は**まだトークンを知らない相手**が正当に問い合わせます ──
多重起動の判定(2.4)と、待機画面の切り替え(2.3)です。`/api/alive`
(心拍)も免除します。トークンを要求すると、**トークンが切れた画面が
黙って死んだ扱いになり**、開いているのに終了してしまいます。どちらも
返すのは状態だけで、業務データを含みません。
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from converter import app_config, screen_lock
from converter.logging_utils import get_logger

from server import Request, Response, json_response, refuse

log = get_logger("app")

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

#: 起動トークンを要求する経路。**業務データを返すものはここに入れる**
TOKEN_REQUIRED_PREFIXES = ("/api/",)

#: そのうち、トークンを要求しないもの(理由はモジュールの説明のとおり)
TOKEN_EXEMPT_PATHS = frozenset({"/api/health", "/api/alive"})

#: 「いま使っている画面か」を確かめない経路。
#: `/api/screen/claim` は**これから受け取る**ための口なので当然、
#: `/api/shutdown` は画面が無くても止められる必要があるためです
#: (`stop.bat` は画面を持ちません)。
#:
#: **ただし停止は素通しではありません。** 起動トークンは画面のHTMLに
#: 入っているので、断られた2枚目のタブも持っています。それだけで止められる
#: と、居ないはずの画面が動いているアプリを落とせてしまいます ── 止めて
#: よいのは「いま使っている画面」か、ローカル領域の合図(`STOP_KEY`)を
#: 読める相手(= 同じ端末の `stop.bat`)だけです(`routes/health.py`)
SCREEN_EXEMPT_PATHS = frozenset({"/api/health", "/api/alive",
                                 "/api/screen/claim", "/api/shutdown"})


class App:
    """組み上がったアプリ1つ分。`dispatch(request) -> Response`。"""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.routes: dict[tuple[str, str], Any] = {}
        #: 自動終了の見張り(`converter.idle_exit.IdleExit`)。起動側が入れます
        self.idle = None
        #: 止め方。`start_app` が差し込みます(ここが待ち受けを知らないように)
        self.shutdown_hook = None

    # -- 状態 -------------------------------------------------------
    def mark_stage(self, stage: str, key: str = "prepare") -> None:
        self.config["STAGE"] = stage
        self.config["STAGE_KEY"] = key

    def mark_ready(self) -> None:
        self.config["READY"] = True
        self.mark_stage("起動完了", "ready")

    def mark_error(self, message: str) -> None:
        self.config["STARTUP_ERROR"] = message

    @property
    def ready(self) -> bool:
        return bool(self.config["READY"])

    # -- 登録 -------------------------------------------------------
    def register(self, blueprint) -> None:
        for key, handler in blueprint.routes.items():
            self.routes[key] = handler

    # -- 受付 -------------------------------------------------------
    def dispatch(self, request: Request) -> Response:
        request.app = self
        if request.path.startswith("/static/"):
            return self._static(request.path[len("/static/"):])

        problem = self._check(request)
        if problem is not None:
            return problem

        handler = self.routes.get((request.method, request.path))
        if handler is None:
            return refuse("そのURLはありません", request.path, status=404)
        return handler(request)

    def _check(self, request: Request) -> Optional[Response]:
        """守りの4つ。**通らなかった理由をそのまま返します。**"""
        host = (request.header("host") or "").split(":")[0]
        if host and host not in ("127.0.0.1", "localhost"):
            log.warning("Host不一致で拒否: %s", request.header("host"))
            return refuse("このアドレスからは利用できません", host, status=400)

        if not any(request.path.startswith(p) for p in TOKEN_REQUIRED_PREFIXES):
            return None

        site = request.header("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            log.warning("別オリジンからの要求を拒否: %s %s", site, request.path)
            return refuse("別のページからは利用できません",
                          "アプリの画面から操作してください", status=403)

        if request.path not in TOKEN_EXEMPT_PATHS:
            supplied = request.header("x-tool-token") or request.arg("t")
            if not secrets.compare_digest(supplied.encode("utf-8"),
                                          str(self.config["TOKEN"]).encode("utf-8")):
                log.warning("トークン不一致で拒否: %s", request.path)
                return refuse("この操作は受け付けられません",
                              "アプリの画面を開き直してください", status=401)

        if request.path not in SCREEN_EXEMPT_PATHS:
            # **通った操作は生存確認を兼ねます。** 画面が動いている以上、
            # 見ていないことにはできません(長い変換の最中は問い合わせが
            # 続くので、心拍が1回落ちても締め出されません)
            if not screen_lock.LOCK.beat(request.header(screen_lock.HEADER)):
                return refuse(
                    "この画面は使えなくなりました",
                    "別の画面で開かれたか、しばらく応答がありませんでした。"
                    "この画面を閉じて、開き直してください。",
                    status=409, screen_lost=True)
        return None

    def _static(self, relative: str) -> Response:
        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return refuse("そのファイルはありません", relative, status=404)
        kind, _enc = mimetypes.guess_type(target.name)
        return Response(body=target.read_bytes(),
                        content_type=f"{kind or 'application/octet-stream'}; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


def create_app(*, token: Optional[str] = None,
               port: Optional[int] = None,
               stop_key: Optional[str] = None) -> App:
    """アプリを1つ組み立てる。

    `token` を省くと起動ごとに新しく作ります。試験からは固定値を渡せます。

    `stop_key` は**画面には渡さない合図**です(`runtime/instance.json` に
    だけ置きます)。停止をブラウザ経由で当てられないようにするためで、
    起動側が入れます。
    """
    app = App({
        "APP_ID": app_config.app_id(),
        "DISPLAY_NAME": app_config.display_name(),
        "VERSION": app_config.version(),
        "PORT": port or app_config.port(),
        # 起動ごとの合言葉(守りの3)。**画面のHTMLに入ります**
        "TOKEN": token or secrets.token_urlsafe(32),
        # 停止のための合図。**画面には入りません**(ローカル領域だけ)
        "STOP_KEY": stop_key or secrets.token_urlsafe(16),
        "STARTED_AT": time.time(),
        # 起動直後は準備中。重い読み込みが終わってから True にします。
        # 待機画面はこれを見て切り替わります
        "READY": False,
        "STAGE": "アプリを準備中",
        "STAGE_KEY": "prepare",
        "STARTUP_ERROR": "",
    })
    _register_routes(app)
    log.info("アプリを組み立てました port=%s 経路=%d",
             app.config["PORT"], len(app.routes))
    return app


def _register_routes(app: App) -> None:
    from .routes import health, input as input_routes, job, screen, settings

    for module in (health, screen, settings, input_routes, job):
        app.register(module.bp)
