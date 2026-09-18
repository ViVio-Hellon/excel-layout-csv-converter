"""URLの受付(Blueprint)

入力を確かめて `converter` へ渡すだけの層です。**業務判断はここに
書きません** ── 変換は `converter.jobs`、設定は `converter.user_settings`、
画面の占有は `converter.screen_lock` が持っています。

受け渡しの形(`Request` / `Response`)は `server.py` が決めています。
ここはそれを再輸出して、各ファイルが1行で取れるようにしているだけです。
"""
from __future__ import annotations

from typing import Callable

from server import (Request, Response, html_response,  # noqa: F401 - 再輸出
                    json_response, refuse)

__all__ = ["Blueprint", "Request", "Response", "html_response",
           "json_response", "refuse"]


class Blueprint:
    """1つの話題ぶんの経路をまとめたもの。

    `app.create_app()` がこれを集めて登録します。Flask の Blueprint と
    同じ役目ですが、**登録表を持つだけ**で他には何もしません。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.routes: dict[tuple[str, str], Callable[[Request], Response]] = {}

    def get(self, path: str):
        return self._add("GET", path)

    def post(self, path: str):
        return self._add("POST", path)

    def _add(self, method: str, path: str):
        def register(func):
            self.routes[(method, path)] = func
            return func
        return register
