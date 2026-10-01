"""変換の実行 ── 進み具合を出し、途中で止められるようにする

【なぜ別スレッドなのか】
100本の変換は分単位でかかります。受け取った通信の中で回すと、その間
画面からの問い合わせに答えられず、**止まったように見えます**。基盤仕様書
3章のレベル2(長時間処理アプリ)が求めているのは、まさにこの区別です:

    処理状態API / 進捗の記録 / 実行中の終了確認 / ブラウザ再接続

【中止のしかた】
`converter.run.convert()` は1本読むごとに `on_file` を呼びます。中止を
頼まれていたらそこで例外を投げ、読み取りを抜けます。**CSVを書くのは
全部読み終えたあと**なので、読み取り中に止めれば書きかけは残りません。
逆に言えば、書き出しが始まってからは止めません ── 4本組のCSVを半分
書いた状態で残すほうが危ないからです。

【数の出どころは変えません】
ここは `converter.run` を呼ぶだけで、数を自分で作りません。画面のために
数え直すと、**同じ数を2通りの道で出す**ことになります(VENDOR.md)。
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import app_config, user_settings
from .logging_utils import get_logger

log = get_logger("jobs")

# 写してある日報管理ツールを読めるようにする。**同じコードを通すため**
_VENDOR = str(app_config.APP_ROOT / "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)


class Cancelled(Exception):
    """利用者が中止を押した。**失敗ではありません。**"""


# 状態の名前。画面にそのまま出すので、ここが唯一の出どころ
READY = "待機中"
RUNNING = "実行中"
DONE = "完了"
STOPPED = "中止しました"
FAILED = "失敗しました"

#: 仕事の種類。画面のタブと同じ
KIND_CONVERT = "変換"
KIND_TALLY = "機側データ集計"

PHASE_READ = "読み取り"
PHASE_WRITE = "CSVの書き出し"
PHASE_TALLY = "機側データ集計"
PHASE_END = "終わりました"


@dataclass
class FileRow:
    """1本ぶんの結果。画面の一覧はこれを並べます。"""

    name: str = ""
    ok: bool = True
    verify_ok: bool = False
    no_totals: bool = False
    report_date: str = ""
    line_text: str = ""
    line: str = ""
    pages: int = 0
    rows: int = 0
    error: str = ""
    verify: str = ""
    #: 読むときに手を入れたこと(作業日の書き方を直した、など)。**黙って直さない**
    note: str = ""
    marks: list[str] = field(default_factory=list)

    @property
    def mark(self) -> str:
        """○ 読めて数も合った / ! 読めたが合わない / × 読めなかった"""
        if not self.ok:
            return "×"
        return "○" if self.verify_ok else "!"


@dataclass
class Job:
    id: str = ""
    kind: str = KIND_CONVERT
    state: str = READY
    phase: str = ""
    total: int = 0
    done: int = 0
    current: str = ""
    dry_run: bool = False
    out_dir: str = ""
    line: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    message: str = ""
    error: str = ""
    files: list[FileRow] = field(default_factory=list)
    written: int = 0
    master_notice: str = ""
    master_ok: bool = False
    #: 機側データ集計(VBAと同じ転記)。頼まれたときだけ動きます
    tally: bool = False
    tally_rows: int = 0
    tally_path: str = ""
    tally_message: str = ""

    # -- 数えかた ---------------------------------------------------
    @property
    def read_ok(self) -> list[FileRow]:
        return [f for f in self.files if f.ok]

    @property
    def verified(self) -> list[FileRow]:
        return [f for f in self.read_ok if f.verify_ok]

    @property
    def suspect(self) -> list[FileRow]:
        """読めたが、数がシートと合わなかったもの。**黙って通しません。**"""
        return [f for f in self.read_ok if not f.verify_ok]

    @property
    def bad(self) -> list[FileRow]:
        return [f for f in self.files if not f.ok]

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    def remaining(self) -> Optional[int]:
        """あと何秒くらいか。**読み取り中だけ**(書き出しは数えません)。"""
        if self.state != RUNNING or self.phase != PHASE_READ or self.done < 1:
            return None
        per = self.elapsed() / self.done
        return max(0, int(per * (self.total - self.done)))

    def snapshot(self) -> dict:
        found = {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "phase": self.phase,
            "running": self.state == RUNNING,
            "total": self.total,
            "done": self.done,
            "percent": int(self.done * 100 / self.total) if self.total else 0,
            "current": self.current,
            "dry_run": self.dry_run,
            "out_dir": self.out_dir,
            "line": self.line,
            "elapsed": int(self.elapsed()),
            "remaining": self.remaining(),
            "message": self.message,
            "error": self.error,
            "written": self.written,
            "master_notice": self.master_notice,
            "master_ok": self.master_ok,
            "tally": self.tally,
            "tally_rows": self.tally_rows,
            "tally_path": self.tally_path,
            "tally_message": self.tally_message,
            "files": [dict(asdict(f), mark=f.mark) for f in self.files],
            "counts": {
                "files": len(self.files),
                "read_ok": len(self.read_ok),
                "verified": len(self.verified),
                "suspect": len(self.suspect),
                "bad": len(self.bad),
            },
        }
        return found


def master_notice() -> tuple[bool, str]:
    """停止内訳マスタが読めるか。**読めないなら、何が空になるかまで言う。**

    記号(`レ` `0` `G`)と分類は、マスタが無くても出ます ── 分類は記号の
    字種で決まるので。**空になるのは「内訳」の1列だけ**です。
    """
    from nippou.access_bridge import stop_master
    from nippou.config import SETTINGS

    path = SETTINGS.stop_reason_master_path
    try:
        by_category = stop_master.load_all_categories()
        codes = {r.code for items in by_category.values()
                 for r in items if r.code}
    except Exception:                             # noqa: BLE001 - 変換は続ける
        codes = set()
    if codes:
        return True, f"停止内訳マスタ: {path}({len(codes)}記号)"
    return False, (f"停止内訳マスタが読めません({path})。"
                   "停止内訳CSVの「内訳」欄が空になります"
                   "(記号と分類は出ます)。設定部で置き場所を指定できます")


def gather(folder: str) -> list[Path]:
    """フォルダから読む相手を集める。**CLIと同じ集めかた**(名前順)。"""
    from . import run
    return run.gather([user_settings.resolve(folder)])


class Runner:
    """変換を1つだけ走らせる。**同時に2つは受けません。**"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self.job = Job()

    # -- 問い合わせ --------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self.job.state == RUNNING

    def snapshot(self) -> dict:
        with self._lock:
            return self.job.snapshot()

    # -- 操作 --------------------------------------------------------
    def start(self, books: list[Path], settings: user_settings.Settings, *,
              dry_run: bool = False, tally: bool = False) -> dict:
        with self._lock:
            if self.job.state == RUNNING:
                raise RuntimeError("すでに変換中です")
            if not books:
                raise ValueError("読む相手がありません(.xlsx / .xlsm)")
            limit = app_config.max_files()
            if len(books) > limit:
                raise ValueError(
                    f"一度に読めるのは {limit} 本までです(いまは {len(books)} 本)。"
                    "分けて実行してください")
            out_dir = "" if dry_run else str(user_settings.ensure_output(settings))
            self._cancel.clear()
            self.job = Job(id=uuid.uuid4().hex[:12], state=RUNNING,
                           phase=PHASE_READ, total=len(books),
                           dry_run=dry_run, out_dir=out_dir, tally=tally,
                           line=settings.line, started_at=time.time())
            job = self.job
        user_settings.apply_to_vendor(settings)
        job.master_ok, job.master_notice = master_notice()
        log.info("変換開始 id=%s %d本 line=%r dry_run=%s 機側データ集計=%s → %s",
                 job.id, job.total, settings.line, dry_run, tally,
                 out_dir or "(書きません)")
        self._thread = threading.Thread(
            target=self._run, args=(books, settings, dry_run, job),
            name="convert", daemon=True)
        self._thread.start()
        return self.snapshot()

    def start_tally(self, sources: list[Path],
                    settings: user_settings.Settings) -> dict:
        """機側データ集計だけを走らせる(変換はしません)。

        タブで分けてあるので、**集計だけ欲しいときに4本組CSVは出ません**。
        受けるのはブック(.xlsx / .xlsm)と、前に出した集計CSV、シートを
        CSVで保存したものです(`converter/tally.py`)。
        """
        with self._lock:
            if self.job.state == RUNNING:
                raise RuntimeError("すでに実行中です")
            if not sources:
                raise ValueError("読む相手がありません(.xlsx / .xlsm / .csv)")
            limit = app_config.max_files()
            if len(sources) > limit:
                raise ValueError(
                    f"一度に読めるのは {limit} 本までです(いまは {len(sources)} 本)。"
                    "分けて実行してください")
            out_dir = str(user_settings.ensure_output(settings))
            self._cancel.clear()
            self.job = Job(id=uuid.uuid4().hex[:12], kind=KIND_TALLY,
                           state=RUNNING, phase=PHASE_TALLY,
                           total=len(sources), out_dir=out_dir, tally=True,
                           started_at=time.time())
            job = self.job
        log.info("機側データ集計 開始 id=%s %d本 → %s", job.id, job.total, out_dir)
        self._thread = threading.Thread(
            target=self._run_tally, args=(sources, job),
            name="tally", daemon=True)
        self._thread.start()
        return self.snapshot()

    def cancel(self) -> bool:
        """中止を頼む。**読み取り中だけ効きます**(書き出しは止めません)。"""
        if not self.running:
            return False
        self._cancel.set()
        with self._lock:
            self.job.message = "中止しています(いま読んでいる1本の区切りまで)"
        log.info("中止を受け付けました id=%s", self.job.id)
        return True

    # -- 中身 --------------------------------------------------------
    def _run(self, books: list[Path], settings: user_settings.Settings,
             dry_run: bool, job: Job) -> None:
        from . import run

        def on_file(one, index, total):
            if self._cancel.is_set():
                raise Cancelled()
            row = FileRow(
                name=one.name, ok=one.ok, error=one.error,
                report_date=one.report_date, line_text=one.line_text,
                line=one.line, pages=one.pages, rows=one.rows,
                verify_ok=bool(one.ok and one.verify.ok),
                no_totals=bool(getattr(one.verify, "no_totals", False)),
                verify=one.verify.summary() if one.ok else "",
                note=getattr(one, "note", ""),
                marks=[l.text() for l in getattr(one.verify, "lines", [])
                       if not l.ok])
            with self._lock:
                job.files.append(row)
                job.done = index
                job.current = one.name
                if index >= total and not dry_run:
                    # **読み終えました。** ここから先は止めません
                    job.phase = PHASE_WRITE
                    job.message = "CSVを書き出しています"

        try:
            found = run.convert([str(b) for b in books], job.out_dir or ".",
                                line=settings.line, dry_run=dry_run,
                                on_file=on_file)
            if job.tally:
                self._tally(books, job, dry_run)
            with self._lock:
                job.written = len(found.written)
                job.state = DONE
                job.phase = PHASE_END
                job.message = self._summary_text(job)
        except Cancelled:
            with self._lock:
                job.state = STOPPED
                job.phase = PHASE_END
                job.message = (f"中止しました({job.done}/{job.total} 本まで"
                               "読みました。CSVは書いていません)")
            log.info("中止しました id=%s %d/%d", job.id, job.done, job.total)
        except Exception as exc:                  # noqa: BLE001 - 画面へ出す
            with self._lock:
                job.state = FAILED
                job.phase = PHASE_END
                job.error = str(exc)
                job.message = "変換を続けられませんでした"
            log.exception("変換が失敗しました id=%s", job.id)
        finally:
            with self._lock:
                job.finished_at = time.time()
                job.current = ""
            log.info("変換おわり id=%s 状態=%s 読めた=%d/%d 数が合った=%d "
                     "書いたCSV=%d %.1f秒", job.id, job.state,
                     len(job.read_ok), len(job.files), len(job.verified),
                     job.written, job.elapsed())

    def _run_tally(self, sources: list[Path], job: Job) -> None:
        """集計だけの仕事。1本ずつ読みながら進み具合を出します。"""
        from . import tally as tally_mod

        def on_file(path, index, total, result):
            if self._cancel.is_set():
                raise Cancelled()
            row = FileRow(name=path.name, ok=bool(result and result.taken),
                          report_date=(result.date if result else ""),
                          rows=(result.rows if result else 0),
                          error=(result.skipped if result else "読めませんでした"),
                          verify=(result.kind if result else ""),
                          verify_ok=bool(result and result.taken))
            with self._lock:
                job.files.append(row)
                job.done = index
                job.current = path.name

        try:
            found = tally_mod.build(sources, on_file=on_file)
            path = tally_mod.write_csv(found, job.out_dir) if found.rows else None
            with self._lock:
                job.tally_rows = len(found.rows)
                job.tally_path = str(path) if path else ""
                job.tally_message = found.summary()
                job.written = 1 if path else 0
                job.state = DONE
                job.phase = PHASE_END
                job.message = found.summary() + (
                    "" if path else " ── CSVは書いていません")
            for name, why in found.errors:
                with self._lock:
                    job.files.append(FileRow(name=name, ok=False, error=why))
            log.info("機側データ集計 おわり id=%s %s → %s",
                     job.id, found.summary(), job.tally_path or "(書かず)")
        except Cancelled:
            with self._lock:
                job.state = STOPPED
                job.phase = PHASE_END
                job.message = (f"中止しました({job.done}/{job.total} 本まで"
                               "読みました。CSVは書いていません)")
            log.info("機側データ集計 中止 id=%s", job.id)
        except Exception as exc:                  # noqa: BLE001 - 画面へ出す
            with self._lock:
                job.state = FAILED
                job.phase = PHASE_END
                job.error = str(exc)
                job.message = "機側データ集計を作れませんでした"
            log.exception("機側データ集計で失敗しました id=%s", job.id)
        finally:
            with self._lock:
                job.finished_at = time.time()
                job.current = ""

    def _tally(self, books: list[Path], job: Job, dry_run: bool) -> None:
        """VBAと同じ転記を1本のCSVに出す(`converter/tally.py`)。

        **変換とは別の出口**です。既存の4本組CSVには手を入れません ──
        あちらは日報管理ツールと1バイトも違わないことを確かめてあるので、
        混ぜると確かめ直しが要ります。
        """
        from . import tally as tally_mod

        with self._lock:
            job.phase = PHASE_TALLY
            job.message = "機側データ集計を作っています"
        try:
            found = tally_mod.build(books)
            with self._lock:
                job.tally_rows = len(found.rows)
                job.tally_message = found.summary()
            if not dry_run and found.rows:
                path = tally_mod.write_csv(found, job.out_dir)
                with self._lock:
                    job.tally_path = str(path)
            skipped = [s for s in found.sheets if not s.taken]
            if skipped:
                log.info("機側データ集計: 拾わなかったシート %d 枚(%s)",
                         len(skipped), skipped[0].skipped)
            log.info("機側データ集計: %s", found.summary())
        except Exception as exc:                  # noqa: BLE001 - 変換は済んでいる
            with self._lock:
                job.tally_message = f"機側データ集計を作れませんでした: {exc}"
            log.exception("機側データ集計で失敗しました id=%s", job.id)

    @staticmethod
    def _summary_text(job: Job) -> str:
        """終わったあとの1行。**合わなかったものを先に言います。**"""
        parts = [f"読めた {len(job.read_ok)}/{len(job.files)} 本"]
        if job.bad:
            parts.append(f"読めなかった {len(job.bad)} 本")
        if job.suspect:
            parts.append(f"数が合わない {len(job.suspect)} 本"
                         "(座標がずれているおそれがあります)")
        if job.dry_run:
            parts.append("確かめただけなので、CSVは書いていません")
        else:
            parts.append(f"書いたCSV {job.written} 本")
        if job.tally and job.tally_message:
            parts.append(f"機側データ集計 {job.tally_message}")
        return " / ".join(parts)


#: アプリで1つ
RUNNER = Runner()
