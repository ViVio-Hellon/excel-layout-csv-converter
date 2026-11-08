"""ポートを1つも開かずに、いまの機能が通るか見る道具

【なぜ要るのか】
デスクトップ版(Tauri)は**ポートを使いません**。外枠(Rust)が Python を
子として起動し、標準入出力で要求を渡します。移せるかどうかは
「**ソケットを1つも開かずに、いまの機能が通るか**」で決まります。

    python scripts/ポート無しで通るか見る.py

`socket.socket` を開こうとしたら必ず落ちるようにしてから、画面と操作を
順に叩きます(`app.dispatch` を直接呼ぶ ── 外枠から来る形と同じ)。
`docs/デスクトップ版への移行_見通し.md` の①がこれです。
"""
import json, os, socket, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "vendor"))
sys.path.insert(0, str(HERE / "tests"))
os.environ["XLCSV_LOCAL_DIR"] = tempfile.mkdtemp()

class NoSockets(RuntimeError):
    pass

# 先に読み込む(`ssl` が取り込み時に `socket` を継承するので、禁止は読み込みの
# あとで掛ける ── **移行では `server.py` を読まない形にします**)
from converter import app_config, intake, jobs, screen_lock   # noqa: E402
import app as app_package                                      # noqa: E402
from server import Request                                     # noqa: E402
from tests._fixture import day_sheet, write_xlsx               # noqa: E402

_real_socket = socket.socket

def forbidden(*a, **k):
    raise NoSockets("ソケットを開こうとしました")

def forbid_sockets():
    """**ここから先、ソケットは1つも作れません。**"""
    socket.socket = forbidden
    socket.create_connection = forbidden

app_config.ensure_local_dirs()
out = Path(tempfile.mkdtemp()); books = Path(tempfile.mkdtemp())
write_xlsx(books / "2026.08.23.xlsx", day_sheet())

application = app_package.create_app(token="t", port=0, stop_key="s")
application.mark_ready()

screen_id = ""
def call(method, path, payload=None, *, body=b"", headers=None):
    from urllib.parse import parse_qs
    head = {"content-type": "application/json", "host": "app.localhost",
            "x-tool-token": "t"}
    if screen_id:
        head["x-screen-id"] = screen_id
    head.update({k.lower(): v for k, v in (headers or {}).items()})
    raw = json.dumps(payload).encode() if payload is not None else body
    path, _, query = path.partition("?")
    res = application.dispatch(Request(method=method, path=path,
                                       query=parse_qs(query), body=raw,
                                       headers=head))
    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception:
        data = {"(本文)": f"{len(res.body)}バイト {res.content_type}"}
    return res.status, data

ok = True
def step(name, method, path, payload=None, want=200, **kw):
    global ok
    try:
        status, data = call(method, path, payload, **kw)
    except NoSockets as exc:
        print(f"  × {name:<34} ソケットを開こうとしました"); ok = False; return {}
    mark = "○" if status == want else "×"
    if status != want: ok = False
    shown = {k: data[k] for k in list(data)[:3]}
    print(f"  {mark} {name:<34} {status} {shown}")
    return data

forbid_sockets()
print("―― ソケットを禁止したまま、画面と操作を通す ――")
step("画面(待機・本画面)", "GET", "/")
got = step("画面の受け取り", "POST", "/api/screen/claim", {"tab_id": "tab-A"})
screen_id = got.get("screen_id", "")
step("心拍", "POST", "/api/alive", {"tab": "tab-A", "page": "p1", "state": "visible"})
step("起動確認", "GET", "/api/health")
step("設定を読む", "GET", "/api/settings")
step("設定を保存", "POST", "/api/settings",
     {"input_dir": str(books), "output_dir": str(out), "line": "HVC",
      "ref_dir": "", "stop_csv": "", "log_dir": ""})
step("受け取りを始める", "POST", "/api/input/begin", {"tab": "convert"})
data = (books / "2026.08.23.xlsx").read_bytes()
step("ブックを1冊受け取る", "POST", "/api/input/add?tab=convert&name=2026.08.23.xlsx",
     None, body=data, headers={"Content-Type": "application/octet-stream"})
step("受け取ったものを見る", "GET", "/api/input?tab=convert")
step("フォルダをそのまま読む", "POST", "/api/input/scan", {"tab": "convert"})
step("変換を始める", "POST", "/api/job/start", {"tab": "convert", "dry_run": True})
for _ in range(100):
    status, job = call("GET", "/api/job")
    if (job.get("job") or {}).get("state") != "実行中":
        break
    time.sleep(0.05)
print(f"  ○ 変換の結果                       {(job.get('job') or {}).get('state')}"
      f" 読めた={(job.get('job') or {}).get('done')}")
step("診断情報", "GET", "/api/diagnostics")
step("静的ファイル(JS)", "GET", "/static/js/app.js")
step("終了を頼む", "POST", "/api/shutdown", {"force": True, "stop_key": "s"})

print("\n結論:", "ポートを1つも開かずに全部通りました" if ok else "通らないものがありました")
