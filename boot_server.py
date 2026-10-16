"""待機画面だけを出す、いちばん小さいアプリ(基盤仕様書 2.2 / 2.3)

【なぜ要るのか】
起動でいちばん時間を食うのは、写してある日報管理ツール(42ファイル)の
読み込みです。そのあとに待機画面を出していたら、「待たせるために見せる
もの」を待たせていることになります ── 出す意味がありません。

そこで、待ち受けだけを先に始めます。ここが答えるのは3つだけ:

    GET  /             待機画面(1往復で出る。外部への要求は無し)
    GET  /api/health   まだ準備できていないことを、本体と**同じ形**で返す
    POST /api/shutdown 止める合図に答える。**本体ができる前でも止まります**

読み込むのは `converter.app_config` と `converter.boot_screen` だけです
(業務のモジュールも、変換も読みません)。

【本体との入れ替え】
`Handover` が呼び先を1つ持ちます。本体ができたら `install()` で差し替える
ので、**待ち受けを開き直しません** ── 開き直すと、その瞬間にブラウザの
問い合わせが落ちて、画面が「接続できません」に見えます。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

from converter import app_config, boot_screen
from converter.logging_utils import get_logger

from server import Request, Response, html_response, json_response

log = get_logger("boot_server")

# 本体ができるまでのあいだ、業務の要求に返す答え。
# 404 にすると「無い」に見えるので、**まだであることを言う**
_NOT_READY = {"ok": False, "reason": "起動しています",
              "detail": "しばらくお待ちください。"}


class BootApp:
    """待機画面だけを出すアプリ。

    **状態は外から書き換えられます。** 段が進むたびに `mark_stage` を
    呼び、待機画面の問い合わせがそれを読みます(本体の `App.mark_stage`
    と同じ役目を、本体ができる前の時間帯だけ引き受けます)。
    """

    def __init__(self, port: int, *, stop_key: str = "") -> None:
        self.port = port
        self.stage = "実行環境を確認中"
        self.stage_key = "environment"
        self.startup_error = ""
        self.started_at = time.time()
        self._page: Optional[str] = None
        #: 止める合図。**本体と同じもの**を渡します(`start_app`)
        self.stop_key = stop_key
        #: 止めてと言われたときに呼ぶ先(`server.AppServer.request_stop`)
        self.shutdown_hook: Optional[Any] = None

    # -- 状態 -------------------------------------------------------
    def mark_stage(self, stage: str, key: str = "prepare") -> None:
        self.stage = stage
        self.stage_key = key

    def mark_error(self, message: str) -> None:
        self.startup_error = message

    # -- 中身 -------------------------------------------------------
    def page(self) -> str:
        """待機画面。**1度組み立てて覚えます**(毎回組むほどのものではない)。"""
        if self._page is None:
            from converter.logging_utils import log_paths
            self._page = boot_screen.render(
                display_name=app_config.display_name(),
                version_label=app_config.version_label(),
                app_id=app_config.app_id(),
                poll_ms=app_config.job_poll_ms(),
                home_url="/",
                log_dir=log_paths()["folder"])
        return self._page

    def health(self) -> dict:
        """本体の `/api/health` と**同じ形**で返します。

        形が違うと、待機画面と `launch_guard` の両方が場合分けを持つ
        ことになります。まだ準備できていないことは `ready` が言います。
        """
        return {
            "app_id": app_config.app_id(),
            "display_name": app_config.display_name(),
            "version": app_config.version(),
            "port": self.port,
            "pid": os.getpid(),
            "ready": False,
            "stage": self.stage,
            "stage_key": self.stage_key,
            "startup_error": self.startup_error,
            "uptime_sec": round(time.time() - self.started_at, 1),
            "screen": {"busy": False},
            "job": None,
        }

    # -- 止める -----------------------------------------------------
    def shutdown(self, request: Request) -> Response:
        """**待機画面でも、止める合図には答えます。**

        【なぜ要るのか】
        本体を組み立てられなかったとき(フォルダが欠けた・設定が壊れた・
        部品が無い)、このアプリは**待機画面に理由を出したまま残ります** ──
        理由を読めるようにするためです。ところがここが止める合図に答えて
        いないと、`stop.bat` でも `--force` でも止まらず、Python が残って
        **フォルダを消せなくなります**(タスクマネージャーで切るしかない)。
        実際にそうなった報告があり、ここで答えるようにしました。

        通すのは**停止の合図**(`runtime/instance.json` の `stop_key`)を
        持ってきた相手だけです ── 本体の `POST /api/shutdown` と同じ鍵で、
        起動トークンでは通しません(あれは画面のHTMLにも入ります)。
        画面の持ち主による停止は見ません: 本体がまだ無いので、画面の
        占有そのものがありません。

        **実行中かどうかは確かめません。** 本体ができていないので、
        変換は始められません(止めて失うものがない)。
        """
        payload = request.json()
        key = str(payload.get("stop_key", "")) or request.header("x-stop-key")
        if not (self.stop_key and key == self.stop_key):
            log.warning("停止を断りました(合図が合いません)")
            return json_response(
                {"ok": False, "reason": "停止できません",
                 "detail": "この操作の出どころを確かめられません。"
                           "同じ端末の stop.bat から止めてください"},
                status=403)
        log.info("停止を受け付けました(stop.bat ── まだ待機画面です)")
        if self.shutdown_hook is not None:
            self.shutdown_hook("stop.bat から停止を頼まれました(待機画面)")
        return json_response({"ok": True, "ready": False})

    # -- 受付 -------------------------------------------------------
    def dispatch(self, request: Request) -> Response:
        if request.path == "/api/health":
            return json_response(self.health(),
                                 **{"Cache-Control": "no-store"})
        if request.path == "/api/shutdown" and request.method == "POST":
            return self.shutdown(request)
        if request.path in ("/", ""):
            return html_response(self.page())
        return json_response(_NOT_READY, status=503)


class Handover:
    """呼び先を1つ持ち、あとから差し替えられるようにする。

    待ち受け(`server.AppServer`)には**これ**を渡します。
    """

    def __init__(self, first: Any) -> None:
        self._app = first
        self._lock = threading.Lock()
        self.handed_over = False

    def install(self, app: Any) -> None:
        with self._lock:
            self._app = app
            self.handed_over = True
        log.info("待ち受けを本体へ引き継ぎました")

    @property
    def current(self) -> Any:
        with self._lock:
            return self._app

    def mark_stage(self, stage: str, key: str = "prepare") -> None:
        """いまの呼び先へ段を伝える(待機画面でも本体でも同じ呼び方)。"""
        current = self.current
        if hasattr(current, "mark_stage"):
            current.mark_stage(stage, key)

    def mark_error(self, message: str) -> None:
        current = self.current
        if hasattr(current, "mark_error"):
            current.mark_error(message)

    def dispatch(self, request: Request) -> Response:
        return self.current.dispatch(request)
