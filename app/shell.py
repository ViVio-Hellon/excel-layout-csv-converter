"""画面の外枠に渡す値

`base.html` が必要とする共通の値をここで作ります。画面ごとのルートが
それぞれ集めると渡し忘れが起きるので、**1か所**にまとめます。

【なぜテンプレート機能を持ち込まないのか】
追加ライブラリを入れない方針なので、差し込みは `{{名前}}` の置き換え
だけにしてあります。`base.html` が外枠、`index.html` が中身で、
`{{CONTENT}}` のところへ入ります ── 骨組みを2枚持たないための形です。
"""
from __future__ import annotations

import html
import json
import urllib.parse
from pathlib import Path

from converter import app_config, idle_exit, screen_lock

TEMPLATE_DIR = app_config.APP_ROOT / "app" / "templates"

#: タブのアイコン。**文字とアイコンの出どころはここ1つ**
_FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="6" fill="{color}"/>'
            '<text x="16" y="23" font-size="18" font-family="sans-serif"'
            ' text-anchor="middle" fill="#ffffff">変</text></svg>')


def favicon(color: str = "#1f6feb") -> str:
    return _FAVICON.format(color=color)


def boot_values(config: dict) -> dict:
    """画面の JavaScript が起動時に受け取る値。

    **間隔と上限の出どころはサーバ側ただ1つ** ── 画面にも同じ数を書くと、
    片方を直したときに食い違います(心拍は `idle_exit.HEARTBEAT_MS`)。
    """
    return {
        "app_id": config["APP_ID"],
        "display_name": config["DISPLAY_NAME"],
        "version": config["VERSION"],
        # 起動ごとの合言葉。画面はこれを付けて `/api/*` を呼びます
        "token": config["TOKEN"],
        "screen_header": screen_lock.HEADER,
        "max_files": app_config.max_files(),
        "max_file_mb": app_config.max_file_bytes() // 1024 // 1024,
        "heartbeat_ms": idle_exit.HEARTBEAT_MS,
        "job_poll_ms": app_config.job_poll_ms(),
        "monitor_level": app_config.monitor_level(),
    }


def _version_note() -> str:
    """版の食い違いの知らせ(帯に出す札)。**ふだんは何も出しません。**"""
    note = app_config.version_note()
    if not note:
        return ""
    return (f'<span class="badge warn" title="{html.escape(note)}">'
            f"中身が古いままです</span>")


def render(page: str, config: dict, **values) -> str:
    """`base.html` に `page` を差し込んで返す。"""
    body = (TEMPLATE_DIR / page).read_text(encoding="utf-8")
    frame = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    html = frame.replace("{{CONTENT}}", body)
    fields = {
        "APP_NAME": config["DISPLAY_NAME"],
        "VERSION": app_config.version_label(),
        # exe と中身の版が食い違っていれば、帯に出します(ふだんは空)
        "VERSION_NOTE": _version_note(),
        # `data:` に埋めるので、ここで符号化して渡す
        "FAVICON": urllib.parse.quote(favicon()),
        "BOOT": json.dumps(boot_values(config), ensure_ascii=False),
    }
    fields.update(values)
    for key, value in fields.items():
        html = html.replace("{{" + key + "}}", str(value))
    return html
