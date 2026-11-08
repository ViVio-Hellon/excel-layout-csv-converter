"""受け渡しの形 ── `Request` / `Response` だけ。**運び方は知りません。**

【なぜ分けるのか】
この2つは「要求と応答」を表すただのデータです。ところが置き場所が
`server.py` だったので、画面を組み立てる `app/` を読むだけで
`http.server` → `http.client` → `ssl` まで付いてきていました。

デスクトップ版(Tauri)は**ポートを使いません**。外枠(Rust)が Python を
子として起動し、**標準入出力**で要求を渡します(`bridge.py`)。その道で
HTTP の部品を読むのは筋が通りませんし、「ポートを使わない」と言えません
(実際、ソケットを禁止して確かめる試験は、この取り込みで落ちました)。

そこで**形だけをここへ**置き、運び方は2つに分かれます:

    ブラウザ版   server.py   127.0.0.1 で待ち受ける(http.server)
    デスクトップ版 bridge.py   標準入出力で受け取る(ソケットを開かない)

どちらも `dispatch(request) -> Response` を持つものへ渡すだけです ──
待機画面(`boot_server.BootApp`)も本体(`app.App`)も同じ約束で動きます。

**`server.py` からも同じ名前で借りられます**(再輸出)。移行の途中で
取り込み元が2通りになると、直したほうと直していないほうができます。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Request", "Response", "json_response", "html_response", "refuse"]


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
