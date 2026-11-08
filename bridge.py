#!/usr/bin/env python3
"""デスクトップ版(Tauri)からの入口 ── **ポートを使わない**

ブラウザ版(`Start.vbs` → `start_app.py`)は 127.0.0.1 のポートで待ち受けて、
ブラウザから繋いでもらっていました。デスクトップ版では、窓を持つ外枠
(Rust/Tauri、`src-tauri/`)がこのプロセスを**子として起動**し、
**標準入出力**で要求を渡します。ソケットは1つも開きません。

    外枠(Rust) ──標準入力──▶ bridge.py ──▶ app.dispatch ──▶ Response
               ◀──標準出力──

業務の処理(変換・機側データ集計・設定・ログ)は**そのまま**です。
`app` が受け取るのは `Request(method, path, query, body, headers)` という
素のデータなので(`converter/wire.py`)、運び方だけを差し替えられます。

【やりとりの形】1件 = 見出し1行(JSON)+ 本文(見出しの `len` バイト)

    要求  {"id": 7, "method": "POST", "path": "/api/x", "query": "a=1",
           "headers": [["Content-Type", "application/json"], …], "len": 12}\\n<本文>
    応答  {"id": 7, "status": 200, "headers": [[…], …], "len": 345}\\n<本文>
    知らせ {"event": "started"}\\n                    (`len` なし。本文なし)

本文を JSON に埋めない(base64 にしない)のは、ブック1冊が数十MBになる
ためです。見出しと生のバイト列を分ければ、余計な変換が要りません。

【知らせ】
    started  … 準備ができた(画面を業務画面へ切り替えてよい)
    quit     … 終わってよい(画面の「終了」・窓の×を通った)
    fatal    … 起動できない(`message` `hint` `log_dir`)。外枠が窓に出す

【標準出力を守る】
やりとりに使う標準出力へほかの誰かが1文字でも書くと、以降すべてずれます。
そこで**最初に**本物の標準出力を別に取っておき、ファイル記述子 1 は
標準エラーへ付け替えます(`print` も C 拡張の出力も標準エラーへ行く)。

【待機画面も同じもの】
本体ができるまでは `boot_server.BootApp` が答えます(ブラウザ版と同じ
待機画面・同じ段)。できたら `Handover.install` で差し替えます ──
**画面を読み直さずに**切り替わります。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, Optional

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))
# 写してある日報管理ツール。**同じコードを通すため**にここに置いてあります
sys.path.insert(0, str(APP_ROOT / "vendor"))

#: 同時に捌く要求の数。画面は「操作 + 進捗の見張り + 心拍 + 静的ファイル」を
#: 同時に投げます。書く要求はアプリ側が1つずつ通します(変換中の設定保存は断る)
WORKERS = 8

#: 1件の本文の上限。ブック1冊の上限(`config/app.json` の `max_file_mb`)に
#: 余裕を足したもの。**外枠(`src-tauri/src/bridge.rs` の `MAX_BODY`)と
#: 同じ値**にします(片方だけ直すと、片方が黙って切ります)
MAX_BODY_MARGIN = 1024 * 1024

#: 外枠が画面を読み込む宛先(`app_config.BRIDGE_HOSTS` の1つめ)
DEFAULT_HOST = "app.localhost"


# ==================================================================
# やりとりの形
# ==================================================================
class FrameTooLarge(Exception):
    """本文が上限を超えた1件。**本文は読み捨ててある**ので次の1件から続けられます。"""

    def __init__(self, head: dict, size: int) -> None:
        super().__init__(f"本文が大きすぎます: {size}")
        self.head = head
        self.size = size


def _discard(stream: BinaryIO, size: int) -> None:
    """本文を読み捨てる(区切りを保つ)。メモリには溜めません。"""
    left = size
    while left > 0:
        chunk = stream.read(min(left, 1024 * 1024))
        if not chunk:
            raise EOFError("本文の途中で終わりました")
        left -= len(chunk)


def read_frame(stream: BinaryIO, *, max_body: int) -> Optional[tuple[dict, bytes]]:
    """1件読む。終わり(相手が閉じた)なら `None`。

    本文が上限を超えたら `FrameTooLarge`(読み捨て済み。その1件だけ断れば
    続けられます)。見出しが壊れている・本文の途中で終わったときは、
    区切りが分からないので続けられません(`ValueError` / `EOFError`)。
    """
    line = stream.readline()
    if not line:
        return None
    head = json.loads(line.decode("utf-8"))
    size = int(head.get("len", 0) or 0)
    if size < 0:
        raise ValueError(f"本文の長さが不正です: {size}")
    if size > max_body:
        _discard(stream, size)
        raise FrameTooLarge(head, size)
    body = b""
    while len(body) < size:
        chunk = stream.read(size - len(body))
        if not chunk:
            raise EOFError("本文の途中で終わりました")
        body += chunk
    return head, body


class FrameWriter:
    """応答と知らせを書く。**1件ずつ丸ごと**書きます(複数のスレッドから来る)。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, head: dict, body: bytes = b"") -> None:
        head = dict(head)
        if body or "id" in head:
            head["len"] = len(body)
        data = json.dumps(head, ensure_ascii=False).encode("utf-8") + b"\n" + body
        with self._lock:
            self._stream.write(data)
            self._stream.flush()

    def event(self, name: str, **fields: Any) -> None:
        self.write({"event": name, **fields})


def to_request(head: dict, body: bytes):
    """外枠から来た1件を `Request` にする。**形は `converter/wire.py`。**"""
    from urllib.parse import parse_qs

    from converter.wire import Request

    headers = {str(k).lower(): str(v) for k, v in (head.get("headers") or [])}
    headers.setdefault("host", DEFAULT_HOST)
    return Request(method=(head.get("method") or "GET").upper(),
                   path=head.get("path") or "/",
                   query=parse_qs(head.get("query") or ""),
                   body=body, headers=headers)


def from_response(response) -> tuple[int, list, bytes]:
    """`Response` を見出しと本文に分ける。"""
    headers = [["Content-Type", response.content_type]]
    headers += [[str(k), str(v)] for k, v in (response.headers or {}).items()]
    return response.status, headers, response.body


# ==================================================================
# 標準出力を守る
# ==================================================================
def protect_stdout() -> BinaryIO:
    """本物の標準出力を取っておき、記述子 1 は標準エラーへ向けます。"""
    real = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    return real


# ==================================================================
# 本体(待ち受けの代わり)
# ==================================================================
class Bridge:
    """標準入出力で要求を受け、`dispatch` へ渡すだけのもの。

    **待ち受けません。** 止まるのは3通り ── 相手(外枠)が標準入力を
    閉じた・画面から終了を頼まれた(`quit`)・起動に失敗した(`fatal`)。
    """

    def __init__(self, reader: BinaryIO, writer: FrameWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.handover = None
        self.application = None
        self.token = ""
        self.stop_key = ""
        self._done = threading.Event()
        self.stop_reason = ""
        self._asked = False                       # 画面から1件でも来たか
        self.max_body = 64 * 1024 * 1024          # 準備ができたら設定から直します

    # -- 組み立て ---------------------------------------------------
    def build(self) -> None:
        """待機画面を先に立て、そのあと本体を組み立てます。

        **順番に意味があります。** 1件目の要求は本体を組み立てている最中に
        届くので、先に待機画面が答えられる状態にしておきます
        (ブラウザ版の `start_app.start()` と同じ考え方)。
        """
        import secrets

        import boot_server
        from converter import app_config, awake_clock, intake, user_settings
        from converter import logging_utils
        from converter.logging_utils import get_logger

        refused = logging_utils.use_folder(user_settings.load().log_dir)
        log = get_logger("bridge")
        log.info("デスクトップ版で起動します 版=%s pid=%s 窓=%s ログ=%s",
                 app_config.version(), os.getpid(), exe_pid() or "(不明)",
                 logging_utils.folder())
        if refused:
            log.warning(refused)
        awake_clock.start()
        self.max_body = app_config.max_file_bytes() + MAX_BODY_MARGIN

        # **合言葉は外枠(exe)が決めます。** 外枠は画面から来た要求に自分の
        # 合言葉を付け替えるので、ここで別のものを作ると画面が断られます
        # (窓は出るのに「この操作は受け付けられません」になります)
        self.token = os.environ.get("XLCSV_TOKEN", "") or secrets.token_urlsafe(32)
        self.stop_key = secrets.token_urlsafe(16)
        boot = boot_server.BootApp(0, stop_key=self.stop_key)
        boot.shutdown_hook = self.request_stop
        self.handover = boot_server.Handover(boot)
        self.boot = boot

    def prepare(self) -> None:
        """本体を組み立てて差し替えます(別のスレッドから呼びます)。"""
        import app as app_package
        import start_app
        from converter import intake, user_settings
        from converter.logging_utils import get_logger

        log = get_logger("bridge")
        try:
            self.handover.mark_stage("アプリを準備中", "prepare")
            start_app.warm_up(log)
            if self._done.is_set():
                return
            self.handover.mark_stage("接続を確認中", "connect")
            user_settings.apply_to_vendor(user_settings.load())
            intake.clear_all()                    # 前回の受け取りは持ち越さない

            application = app_package.create_app(token=self.token, port=0,
                                                 stop_key=self.stop_key)
            application.shutdown_hook = self.request_stop
            # **デスクトップ版であることを本体へ**(診断情報の「待ち受け」の
            # 言い方が変わります ── ポートを使っていないのに番号を出すと、
            # 調べる人がそこを見に行きます)
            application.config["BRIDGE"] = True
            # **自動終了の見張りは立てません。** 窓を閉じれば標準入力が閉じ、
            # このプロセスも終わります ── 見張る相手(タブ)が居ません
            application.idle = None
            self.handover.install(application)
            application.mark_ready()
            self.application = application
            self.writer.event("started", url=f"http://{DEFAULT_HOST}/")
            log.info("起動完了(デスクトップ版)")
        except Exception as exc:                  # noqa: BLE001 - 理由を窓へ
            log.exception("起動の途中で失敗しました")
            self.handover.mark_error(str(exc))
            self.fatal(f"起動できませんでした: {exc}",
                       hint="ログの error.log に中身(traceback)があります")

    # -- 止まる -----------------------------------------------------
    def request_stop(self, reason: str = "") -> None:
        """画面から終了を頼まれた。**外枠へ知らせてから終わります。**"""
        if self._done.is_set():
            return
        self.stop_reason = reason or "終了を頼まれました"
        self.writer.event("quit", reason=self.stop_reason)
        self._done.set()

    def fatal(self, message: str, *, hint: str = "") -> None:
        """起動できない。**外枠が窓に理由を出します。**"""
        from converter.logging_utils import log_paths

        self.writer.event("fatal", message=message, hint=hint,
                          log_dir=log_paths()["folder"])
        self._done.set()

    @property
    def stopping(self) -> bool:
        return self._done.is_set()

    # -- 受け付け ---------------------------------------------------
    def answer(self, head: dict, body: bytes) -> None:
        """1件ぶん。**1件の失敗で止めません。**"""
        from converter.logging_utils import get_logger

        if not self._asked:
            # **窓が本当に画面を読み込んだ**ことの記録(1回だけ)。
            # 「窓は出たのに真っ白」を切り分けられるようにします
            self._asked = True
            get_logger("bridge").info("画面から最初の要求が来ました: %s %s",
                                      head.get("method"), head.get("path"))
        try:
            response = self.handover.dispatch(to_request(head, body))
            status, headers, data = from_response(response)
        except Exception:                         # noqa: BLE001
            get_logger("bridge").exception("要求を処理できませんでした: %s",
                                           head.get("path"))
            status = 500
            headers = [["Content-Type", "application/json; charset=utf-8"]]
            data = json.dumps({"ok": False, "reason": "内部で失敗しました",
                               "detail": "ログの error.log を見てください"},
                              ensure_ascii=False).encode("utf-8")
        self.writer.write({"id": head.get("id"), "status": status,
                           "headers": headers}, data)

    def serve_forever(self) -> None:
        """標準入力から要求を読み、別のスレッドで答えます。"""
        from converter.logging_utils import get_logger

        log = get_logger("bridge")
        pool = ThreadPoolExecutor(max_workers=WORKERS,
                                  thread_name_prefix="bridge")
        try:
            while not self._done.is_set():
                try:
                    found = read_frame(self.reader, max_body=self.max_body)
                except FrameTooLarge as big:
                    log.warning("本文が大きすぎます: %s バイト(%s)",
                                big.size, big.head.get("path"))
                    self.writer.write(
                        {"id": big.head.get("id"), "status": 413,
                         "headers": [["Content-Type",
                                      "application/json; charset=utf-8"]]},
                        json.dumps({"ok": False, "reason": "大きすぎます",
                                    "detail": f"1件に送れるのは "
                                              f"{self.max_body // 1024 // 1024}MB "
                                              "までです"},
                                   ensure_ascii=False).encode("utf-8"))
                    continue
                except (ValueError, EOFError) as exc:
                    log.error("やりとりの区切りが分からなくなりました: %s", exc)
                    break
                if found is None:
                    log.info("外枠が標準入力を閉じました。終わります")
                    break
                head, body = found
                pool.submit(self.answer, head, body)
        finally:
            pool.shutdown(wait=False)


# ==================================================================
# 窓(exe)の番号
# ==================================================================
def exe_pid() -> int:
    """外枠(exe)の番号。環境変数が無ければ親の番号。

    `stop.bat` と「ブラウザ版と同時に動かさない」判定が使います
    (`launch_guard` のデスクトップ版の印)。Python の親は `py.exe` の
    こともあるので、外枠が環境変数で渡します。
    """
    try:
        found = int(os.environ.get("XLCSV_EXE_PID", "") or 0)
    except ValueError:
        found = 0
    return found or os.getppid()


# ==================================================================
# 入口
# ==================================================================
def main(argv: Optional[list] = None) -> int:
    real_stdout = protect_stdout()
    writer = FrameWriter(real_stdout)
    bridge = Bridge(sys.stdin.buffer, writer)
    try:
        bridge.build()
    except Exception as exc:                      # noqa: BLE001 - 理由を窓へ
        writer.event("fatal", message=f"起動できませんでした: {exc}",
                     hint="Python の入れ方・フォルダの場所を確かめてください",
                     detail=traceback.format_exc()[-800:])
        return 1

    from converter import intake, launch_marker
    from converter.logging_utils import get_logger

    log = get_logger("bridge")

    # **ブラウザ版と同時には動きません**(あとから開いたほうが止まります)
    other = launch_marker.browser_running()
    if other:
        log.warning("ブラウザ版が起動しているので、デスクトップ版は終わります: %s",
                    other.get("detail", ""))
        bridge.fatal("ブラウザ版が起動しています",
                     hint="先にブラウザ版の画面で「終了」するか、"
                          "stop.bat を実行してからもう一度開いてください")
        return 2

    launch_marker.write_desktop(pid=os.getpid(), exe_pid=exe_pid())
    try:
        threading.Thread(target=bridge.prepare, name="prepare",
                         daemon=True).start()
        bridge.serve_forever()
    finally:
        launch_marker.remove_desktop()
        intake.clear_all()
        log.info("片づけました(デスクトップ版)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
