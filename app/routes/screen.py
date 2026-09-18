"""画面の占有 ── **2枚目は開かせない**

判定そのものは `converter.screen_lock` にあります。ここはその口です。

`claim` だけは「いま使っている画面か」の確認を免除しています
(`app.SCREEN_EXEMPT_PATHS`)── **これから受け取る**ための口なので、
持っていないと通れないことにすると誰も受け取れません。
"""
from __future__ import annotations

from converter import screen_lock
from converter.logging_utils import get_logger

from . import Blueprint, Request, Response, json_response

log = get_logger("app.screen")

bp = Blueprint("screen")


@bp.post("/api/screen/claim")
def claim(request: Request) -> Response:
    payload = request.json()
    tab_id = str(payload.get("tab_id", ""))
    found = screen_lock.LOCK.claim(tab_id, str(payload.get("load_id", "")),
                                   request.header("user-agent"))
    if not found.ok:
        log.info("2枚目の画面を断りました: %s", found.detail)
        return json_response({"ok": False, "reason": found.reason,
                              "detail": found.detail,
                              "retry_after": found.retry_after}, status=409)
    log.info("画面を渡しました tab=%s", tab_id[:8])
    return json_response({"ok": True, "screen_id": found.screen_id})


@bp.post("/api/screen/release")
def release(request: Request) -> Response:
    """閉じたぶんは空ける。**そうしないと、次に開くまで待たされます。**"""
    screen_lock.LOCK.release(request.header(screen_lock.HEADER))
    return json_response({"ok": True})
