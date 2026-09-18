"""Webサーバだけを動かす(基盤仕様書 2.5 / 4.2)

ここは**待ち受けて、`app.routes` へ渡して、返すだけ**です。環境の確認も
多重起動の判定もしません ── それは `start_app.py` と `launch_guard.py` の
担当です。この線を引いておくと、「起動で失敗したのか、Webで失敗したのか」
がログと責務の両方で分かれます。

追加ライブラリは使いません(`http.server`)。現場のPCへ配るものなので、
入れるものが増えるほど「動かない」理由が増えます。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import appinfo, jobs, routes, screen

log = logging.getLogger("xlcsv.app")

#: 1回の書き込みで受ける最大。ブック1本ぶん(上限)に少し足したもの
MAX_BODY = appinfo.max_file_bytes() + 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "ExcelCsvConverter"
    protocol_version = "HTTP/1.1"

    # -- 受ける ------------------------------------------------------
    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(routes.refuse(
                "大きすぎます",
                f"1回に送れるのは {MAX_BODY // 1024 // 1024}MB までです",
                status=413))
            self.close_connection = True
            return
        body = self.rfile.read(length) if length else b""
        request = routes.Request(
            method=method, path=parsed.path,
            query=parse_qs(parsed.query), body=body,
            headers={k.lower(): v for k, v in self.headers.items()})
        try:
            response = routes.dispatch(request)
        except Exception as exc:                  # noqa: BLE001 - 落とさない
            log.exception("要求を捌けませんでした: %s %s", method, parsed.path)
            response = routes.json_response(
                {"ok": False, "reason": "アプリの中で失敗しました",
                 "detail": str(exc)}, status=500)
        self._send(response)

    def do_GET(self) -> None:                     # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:                    # noqa: N802
        self._handle("POST")

    def do_OPTIONS(self) -> None:                 # noqa: N802
        """待機画面(file://)からの問い合わせ用。**起動確認だけ**開けます。"""
        allow = self.path.startswith("/api/health")
        self.send_response(204 if allow else 405)
        if allow:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- 返す --------------------------------------------------------
    def _send(self, response: routes.Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        # 画面を組み込みで開かれると「2枚目」の判定をすり抜けられます
        self.send_header("X-Frame-Options", "DENY")
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if response.body:
            self.wfile.write(response.body)

    # -- ログ --------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:   # noqa: A003
        """標準エラーへ垂れ流さず、アプリのログへ。**成功は残しません。**

        1秒おきの生存確認と進捗の問い合わせで、ログはすぐ埋まります。
        残す値打ちがあるのは失敗だけです(基盤仕様書 2.6)。
        """
        try:
            status = int(args[1])
        except (IndexError, ValueError):
            status = 0
        if status >= 400:
            log.warning("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args) -> None:     # noqa: A003
        log.warning("%s - %s", self.address_string(), fmt % args)


class Server:
    """待ち受けと、終わり方をまとめたもの。"""

    def __init__(self, host: str = "", port: int = 0) -> None:
        self.host = host or appinfo.host()
        self.port = port or appinfo.port()
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def bind(self) -> ThreadingHTTPServer:
        """束縛する。**ここで失敗したら、それが多重起動の最後の歯止め**です。"""
        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        return self.httpd

    def start_serving(self) -> None:
        """待ち受けを始める。**まだ「起動完了」とは言いません。**

        束縛できた時点で起動確認APIは答えられますが、答えられることと
        使えることは別です。`ready` を立てるのは呼んだ側 ── 準備(写して
        ある日報管理ツールの読込)が終わってからです。待機画面はその
        `ready` を見て切り替わります(基盤仕様書 2.3)。
        """
        httpd = self.httpd or self.bind()
        routes.RUNTIME.stop_key = routes.RUNTIME.stop_key or secrets.token_urlsafe(16)
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.3},
            name="http", daemon=True)
        self._thread.start()
        log.info("待ち受けています %s", appinfo.base_url())
        threading.Thread(target=self._watch_idle, name="idle", daemon=True).start()

    def mark_ready(self) -> None:
        routes.RUNTIME.ready = True
        routes.RUNTIME.stage = "起動完了"

    def wait_until_stopped(self) -> str:
        """止めてと言われるまで待つ。**終わった理由を返します。**"""
        httpd = self.httpd
        try:
            while not routes.RUNTIME.shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            routes.RUNTIME.request_stop("Ctrl+C で止められました")
        finally:
            routes.RUNTIME.ready = False
            if httpd:
                httpd.shutdown()
                httpd.server_close()
        return routes.RUNTIME.stop_reason or "止められました"

    def serve_forever_until_stopped(self) -> str:
        self.start_serving()
        self.mark_ready()
        return self.wait_until_stopped()

    # -- 画面が無くなったあと ----------------------------------------
    def _watch_idle(self) -> None:
        """画面が無いまま時間が経ったら、自分で終わる(基盤仕様書 2.9)。

        **変換中は終わりません。** ブラウザを閉じても書き出しは終わらせます
        ── 4本組のCSVを半分書いた状態で残すほうが危ないからです。
        逆に、終わったあとも残り続けると次の起動が「すでに起動しています」
        で断られ続けるので、誰も見ていないなら畳みます。
        """
        minutes = appinfo.idle_exit_minutes()
        if minutes <= 0:
            return
        limit = minutes * 60
        since = time.time()
        while not routes.RUNTIME.shutdown.is_set():
            time.sleep(5)
            if screen.LOCK.busy() or jobs.RUNNER.running:
                since = time.time()
                continue
            if time.time() - since > limit:
                log.info("画面が %d 分ありませんでした。終わります", minutes)
                routes.RUNTIME.request_stop(
                    f"画面が {minutes} 分無かったので終わりました")
                return


def instance_info(stop_key: str = "") -> dict:
    """`runtime/instance.json` に書く中身(停止と多重起動の判定に使います)。"""
    import os
    import sys as _sys
    return {
        "app_id": appinfo.app_id(),
        "name": appinfo.display_name(),
        "version": appinfo.version(),
        "pid": os.getpid(),
        "port": appinfo.port(),
        "url": appinfo.base_url(),
        "python": _sys.executable,
        "app_root": str(appinfo.APP_ROOT),
        "started_at": time.time(),
        "stop_key": stop_key,
    }


def describe_bind_error(exc: OSError) -> str:
    """束縛に失敗した理由を、次に何をすればよいか分かる言葉にする。"""
    return (f"ポート {appinfo.port()} を使えません({exc})。\n"
            "  ・このアプリがすでに起動している場合は、その画面を使ってください\n"
            "  ・別のアプリが使っている場合は config/app.json の "
            "server.port を変えてください")


if __name__ == "__main__":        # 調べるとき用。通常は start_app.py から
    from app import logging_setup
    logging_setup.application(console=True)
    logging_setup.quiet_vendor()
    srv = Server()
    try:
        srv.bind()
    except OSError as error:
        raise SystemExit(describe_bind_error(error))
    print(json.dumps(instance_info(), ensure_ascii=False, indent=2))
    print(srv.serve_forever_until_stopped())
