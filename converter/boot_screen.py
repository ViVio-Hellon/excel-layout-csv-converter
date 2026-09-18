r"""起動待機画面(基盤仕様書 2.2 / 2.3)

【なぜ独立したモジュールなのか】
この画面は**アプリ本体を読み込む前に**出さなければなりません。起動で
時間を食うのは写してある日報管理ツール(42ファイル)の読み込みで、
そのあとに待機画面を出していたら「待たせるために見せるもの」を待たせて
いることになります ── 出す意味がありません。

そこでここは**この1ファイルだけで完結**させ、`boot_server` が最小の
受付で先に出せるようにしてあります。本体が組み上がったあとも同じ関数を
`/` が呼ぶので、**骨格は1か所**です。

【1往復で出す】
外部への要求(CSS・画像・書体)を1つも出しません。色は
`app/static/css/tokens.css` を読んで**そのまま埋め込みます** ── 色の
出どころを2つにしないための読み込みで、読めなければ手元の控えで描きます。

【秒数で完了扱いにしない】
何秒経ったかではなく、**バックエンドの応答**を見て切り替えます。しかも
応答すればよいのではなく、`app_id` が一致し `ready` が立ってからです ──
同じポートを別のアプリが使っていても、誤って「起動した」と言わないため。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: 色の出どころ(設計:`tokens.css` ただ1つ)
_TOKENS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "tokens.css"

#: `tokens.css` が読めなかったときの控え。**起動画面が出ないよりはよい**
_FALLBACK = {
    "--boot-bg": "#0f1720", "--boot-panel": "#172230",
    "--boot-line": "#24344a", "--boot-text": "#e8eef6",
    "--boot-dim": "#93a4b8", "--accent": "#4da3ff", "--warn": "#ffb648",
}

_DECL = re.compile(r"(--[a-z-]+)\s*:\s*([^;]+);")


def colors() -> dict[str, str]:
    """`tokens.css` の `:root` から色を読む。読めなければ控えを返す。"""
    found = dict(_FALLBACK)
    try:
        text = _TOKENS.read_text(encoding="utf-8")
    except OSError:
        return found
    head = text.split("@media")[0]                # 明るいほうの :root だけ
    for name, value in _DECL.findall(head):
        found[name] = value.strip()
    return found


#: 出す段。**細かくしすぎない**(基盤仕様書 2.2)
STAGES: tuple[tuple[str, str], ...] = (
    ("environment", "実行環境を確認中"),
    ("prepare", "アプリを準備中"),
    ("connect", "接続を確認中"),
    ("ready", "起動完了"),
)

#: これを過ぎたら「どこを見ればよいか」を出す(秒)
SLOW_SEC = 45


def render(*, display_name: str, version_label: str, app_id: str,
           poll_ms: int = 400, home_url: str = "/", log_dir: str = "") -> str:
    """待機画面のHTML。**外部への要求を1つも持ちません。**"""
    token = colors()
    style = "\n".join(f"      {k}: {v};" for k, v in sorted(token.items())
                      if k.startswith("--boot") or k in ("--accent", "--warn"))
    boot = json.dumps({"app_id": app_id, "poll_ms": poll_ms,
                       "home_url": home_url, "slow_sec": SLOW_SEC,
                       "stages": [k for k, _label in STAGES]},
                      ensure_ascii=False)
    items = "\n".join(
        f'    <li id="stage-{key}"><span class="dot"></span>{label}</li>'
        for key, label in STAGES)
    return _PAGE.format(display_name=_escape(display_name),
                        version_label=_escape(version_label),
                        log_dir=_escape(log_dir or "(ログの場所は起動後に出ます)"),
                        style=style, items=items, boot=boot)


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{display_name} を起動しています</title>
<style>
  :root {{
{style}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: var(--boot-bg); color: var(--boot-text);
    font-family: "Yu Gothic UI", "Meiryo", sans-serif; padding: 24px;
  }}
  .panel {{
    width: min(560px, 100%); background: var(--boot-panel);
    border: 1px solid var(--boot-line); border-radius: 14px; padding: 28px 30px;
  }}
  h1 {{ margin: 0 0 4px; font-size: 19px; }}
  .sub {{ color: var(--boot-dim); font-size: 13px; margin-bottom: 22px; }}
  ul {{ list-style: none; margin: 0 0 20px; padding: 0; }}
  li {{
    display: flex; align-items: center; gap: 10px; padding: 7px 0;
    color: var(--boot-dim); font-size: 14px;
  }}
  li.on {{ color: var(--boot-text); }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; flex: none;
         background: var(--boot-line); }}
  li.on .dot {{ background: var(--accent); animation: pulse 1.1s infinite; }}
  li.done .dot {{ background: #2e7d5b; }}
  @keyframes pulse {{ 50% {{ opacity: .35; }} }}
  .bar {{ height: 4px; background: var(--boot-bg); border-radius: 3px; overflow: hidden; }}
  .bar span {{ display: block; height: 100%; width: 35%; background: var(--accent);
              animation: slide 1.5s ease-in-out infinite; }}
  @keyframes slide {{ 0% {{ margin-left: -35%; }} 100% {{ margin-left: 100%; }} }}
  .foot {{ display: flex; justify-content: space-between; margin-top: 16px;
          color: var(--boot-dim); font-size: 12px; }}
  .trouble {{
    display: none; margin-top: 22px; padding: 16px; border-radius: 10px;
    background: #2a1f14; border: 1px solid #5a3f1d; font-size: 13px; line-height: 1.7;
  }}
  .trouble.show {{ display: block; }}
  .trouble b {{ color: var(--warn); }}
  code {{ display: block; margin-top: 6px; padding: 8px 10px; background: var(--boot-bg);
         border-radius: 6px; font-size: 12px; word-break: break-all; }}
</style>
</head>
<body>
<div class="panel">
  <h1>{display_name}</h1>
  <div class="sub">{version_label} — 起動しています。しばらくお待ちください。</div>
  <ul>
{items}
  </ul>
  <div class="bar"><span></span></div>
  <div class="foot">
    <span id="stage-text">実行環境を確認中</span>
    <span id="elapsed">0 秒</span>
  </div>
  <div class="trouble" id="trouble">
    <b id="trouble-title">時間がかかっています</b>
    <div id="trouble-body">
      まだ起動していません。しばらく待っても変わらない場合は、次のログを確認してください。
    </div>
    <code>{log_dir}</code>
    <div style="margin-top:10px">
      それでも起動しない場合は <b>stop.bat</b> を実行してから、
      <b>start.bat</b>(診断用)で起動すると、コンソールに理由が出ます。
    </div>
  </div>
</div>
<script>
(function () {{
  var BOOT = {boot};
  var started = Date.now();
  function at(key, label) {{
    var hit = false;
    BOOT.stages.forEach(function (name) {{
      var el = document.getElementById("stage-" + name);
      if (!el) {{ return; }}
      el.className = hit ? "" : (name === key ? "on" : "done");
      if (name === key) {{ hit = true; }}
    }});
    document.getElementById("stage-text").textContent = label || key;
  }}
  function trouble(title, body) {{
    document.getElementById("trouble-title").textContent = title;
    if (body) {{ document.getElementById("trouble-body").textContent = body; }}
    document.getElementById("trouble").className = "trouble show";
  }}
  setInterval(function () {{
    document.getElementById("elapsed").textContent =
      Math.floor((Date.now() - started) / 1000) + " 秒";
  }}, 250);
  function poll() {{
    fetch("/api/health", {{ cache: "no-store" }})
      .then(function (r) {{ return r.json(); }})
      .then(function (health) {{
        if (health.app_id !== BOOT.app_id) {{
          at("environment", "別のアプリが応答しました");
          trouble("別のアプリが同じポートを使っています",
                  "応答したのは「" + (health.display_name || health.app_id) +
                  "」でした。config/app.json の server.port を変えてください。");
          return setTimeout(poll, 2000);
        }}
        if (health.ready) {{
          at("ready", "起動完了");
          return setTimeout(function () {{ location.replace(BOOT.home_url); }}, 250);
        }}
        at(health.stage_key || "prepare", health.stage || "アプリを準備中");
        if (health.startup_error) {{ trouble("起動できませんでした", health.startup_error); }}
        setTimeout(poll, BOOT.poll_ms);
      }})
      .catch(function () {{
        at("environment", "実行環境を確認中");
        if (Date.now() - started > BOOT.slow_sec * 1000) {{
          trouble("起動できていないかもしれません");
        }}
        setTimeout(poll, 500);
      }});
  }}
  at("environment", "実行環境を確認中");
  poll();
}})();
</script>
</body>
</html>
"""
