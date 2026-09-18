"""Webサーバの起動だけを担当する(基盤仕様書 2.5)

起動監視(`launch_guard.py`)とWeb処理(`app/`)の境界。ここは「どの
アプリを、どのポートで、どう止めるか」しか知りません。業務も画面も
持たないので、**起動で失敗したのか、Webで失敗したのか**がログと責務の
両方で分かれます。

追加ライブラリは使いません(`http.server`)。現場のPCへ配るものなので、
入れるものが増えるほど「動かない」理由が増えます。

【受け渡しの形】
WSGI の代わりに、このファイルが決める `Request` / `Response` の2つだけを
約束にします。待機画面(`boot_server.BootApp`)も本体(`app.App`)も
`dispatch(request) -> Response` を持っていればよく、**待ち受けを開き
直さずに入れ替えられます**(`boot_server.Handover`)。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from converter import app_config
from converter.logging_utils import get_logger

log = get_logger("server")

#: 1回の書き込みで受ける最大。ブック1本ぶん(上限)に少し足したもの
MAX_BODY = app_config.max_file_bytes() + 1024 * 1024

#: 停止要求を受けてから閉じるまでの猶予(秒)。応答を返しきる前に止めると
#: 「押したのに何も起きなかった」ように見えます
GRACEFUL_TIMEOUT_SEC = 3


# ------------------------------------------------------------------
# 受け渡しの形
# ------------------------------------------------------------------
@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    #: 受け取ったアプリ自身。Flask の `current_app` にあたるものを
    #: **明示して渡します** ── スレッドローカルを1つ増やさないため
    app: Any = None

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            found = json.loads(self.body.decode("utf-8"))
            return found if isinstance(found, dict) else {}
        except Exception:                          # noqa: BLE001
            return {}

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def arg(self, name: str, default: str = "") -> str:
        values = self.query.get(name) or []
        return values[0] if values else default


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)


def json_response(payload: dict, status: int = 200, **headers) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(status=status, body=body, headers=headers)


def html_response(html: str, status: int = 200) -> Response:
    return Response(status=status, body=html.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store, must-revalidate"})


def refuse(reason: str, detail: str = "", status: int = 409, **extra) -> Response:
    """断りは**理由を必ず添えます**(何をすればよいかまで書く)。"""
    return json_response({"ok": False, "reason": reason, "detail": detail,
                          **extra}, status=status)


# ------------------------------------------------------------------
# 翻訳するだけの受付
# ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ExcelCsvConverter"
    protocol_version = "HTTP/1.1"

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(refuse("大きすぎます",
                              f"1回に送れるのは {MAX_BODY // 1024 // 1024}MB までです",
                              status=413))
            self.close_connection = True
            return
        body = self.rfile.read(length) if length else b""
        application = self.server.application            # type: ignore[attr-defined]
        request = Request(method=method, path=parsed.path,
                          query=parse_qs(parsed.query), body=body,
                          headers={k.lower(): v for k, v in self.headers.items()})
        try:
            response = application.dispatch(request)
        except Exception as exc:                   # noqa: BLE001 - 落とさない
            log.exception("要求を捌けませんでした: %s %s", method, parsed.path)
            response = json_response({"ok": False, "reason": "アプリの中で失敗しました",
                                      "detail": str(exc)}, status=500)
        self._send(response)

    def do_GET(self) -> None:                      # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:                     # noqa: N802
        self._handle("POST")

    def _send(self, response: Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        # 組み込みで開かれると「2枚目」の判定をすり抜けられます
        self.send_header("X-Frame-Options", "DENY")
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if response.body:
            self.wfile.write(response.body)

    def log_message(self, fmt: str, *args) -> None:    # noqa: A003
        """**成功は残しません。**

        心拍と進捗の問い合わせでログはすぐ埋まります。残す値打ちが
        あるのは失敗だけです(基盤仕様書 2.6)。
        """
        try:
            status = int(args[1])
        except (IndexError, ValueError):
            status = 0
        if status >= 400:
            log.warning("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args) -> None:      # noqa: A003
        log.warning("%s - %s", self.address_string(), fmt % args)


# ------------------------------------------------------------------
# 待ち受け
# ------------------------------------------------------------------
class AppServer:
    """待ち受けているサーバ1つ分。

    `application` は `dispatch(request) -> Response` を持つもの。起動中は
    待機画面(`boot_server.Handover`)を渡しておき、本体ができたら
    そちらが中身を差し替えます ── **開き直しません。** 開き直すと、
    その瞬間にブラウザの問い合わせが落ちて「接続できません」に見えます。
    """

    def __init__(self, application, *, host: str = "", port: int = 0) -> None:
        self.application = application
        self.host = host or app_config.host()
        self.port = port or app_config.port()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self.stop_reason = ""

    def bind(self) -> ThreadingHTTPServer:
        """束縛する。**ここで失敗したら、それが多重起動の最後の歯止め**です。"""
        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.application = self.application     # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        return self.httpd

    def start_serving(self) -> None:
        """待ち受けを始める。**まだ「起動完了」とは言いません。**

        束縛できた時点で起動確認APIは答えられますが、答えられることと
        使えることは別です。`ready` を立てるのはアプリ側で、待機画面は
        それを見て切り替わります(基盤仕様書 2.3)。
        """
        httpd = self.httpd or self.bind()
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.3},
            name="http", daemon=True)
        self._thread.start()
        log.info("待ち受けています http://%s:%s/", self.host, self.port)

    def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        self._stopped.set()

    @property
    def stopping(self) -> bool:
        return self._stopped.is_set()

    def wait_until_stopped(self) -> str:
        """止めてと言われるまで待つ。**終わった理由を返します。**"""
        try:
            while not self._stopped.wait(0.5):
                pass
        except KeyboardInterrupt:
            self.request_stop("Ctrl+C で止められました")
        finally:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        return self.stop_reason or "止められました"


def wait_until_listening(port: int, *, host: str = "",
                         timeout: float = 10.0) -> bool:
    """そのポートが受け付けるようになるまで待つ(起動の確認に使う)。"""
    host = host or app_config.host()
    limit = time.time() + timeout
    while time.time() < limit:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def describe_bind_error(exc: OSError) -> str:
    """束縛に失敗した理由を、次に何をすればよいか分かる言葉にする。"""
    return (f"ポート {app_config.port()} を使えません({exc})。\n"
            "  ・このアプリがすでに起動している場合は、その画面を使ってください\n"
            "  ・別のアプリが使っている場合は config/app.json の "
            "server.port を変えてください")
