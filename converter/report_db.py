r"""共有の日報データを**読むだけ** ── ロットを探すの本命

【これは何か】
日報管理ツール(all-tools)で各端末が「共有へ保存」を押すと、行は共有の
`日報データ.sqlite3` へ送られます(向こうの設定の `access_db_path`)。

    T_日報ヘッダー_<ライン>   報告日・ライン・直・頁・担当者 …
    T_日報明細_<ライン>       報告日・ライン・直・頁・行番号・LOT・ZAI …

**控え(LocalBackup)とは別もの**です。控えはその端末の中に置く写しで、
ほかの端末の行は入っていません。全ライン・全端末の行が集まっているのは
こちらです ── 向こうの「ロットを探す」もここを読んでいます
(`services/shared_reports`)。

【なぜ読むのか】
集計明細CSVは**集計から書かれます**。日報管理ツールの「集計から除く」で
外した作業者のページは集計に入らないので、CSVにも出ません。紙(印刷用
HTML)から補うこともできますが(`plate_paper`)、紙は刷った/共有へ保存した
ページにしかありません。**ここを読めば、向こうとまったく同じ範囲**に
なります。

【決まり】
- **読むだけ。** `?mode=ro` で開きます ── 無いファイルを作らず、書けません
- **待たせません。** 開くのも問い合わせも合わせて `SECONDS` 秒で見切り、
  当たった行は `CAP` 行で止めます。共有に届かないときは、確かめるだけで
  何十秒も止まることがあります
- **読めなくても探すのをやめません。** 理由を画面に出して、集計明細CSVと
  紙で続けます(向こうも同じ振る舞いです)
- **Access(.accdb)は読みません。** ロット№の比べ方(全角・小文字を
  揃えてから頭を比べる)は Access の SQL では書けず、表を丸ごと書き出す
  ことになります ── 向こうも同じ線引きで読んでいません
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from . import plate_source
from .logging_utils import get_logger

log = get_logger("plate.report_db")

#: 表の名前の頭(向こうの `access_*_table_template` の既定)
HEADER_PREFIX = "T_日報ヘッダー_"
DETAIL_PREFIX = "T_日報明細_"

#: ページの列。VBA が作った表は `頁`、途中の版のものは `ページ`
PAGE_NAMES = ("頁", "ページ")

#: 読むのに待つ秒数(開くのも問い合わせも、まとめてこの中で)
SECONDS = 8.0

#: 持ってくる当たりの行の上限。**画面の上限の10倍**(ここで切れたら、
#: どのみち「絞ってください」と言うことになります)
CAP = 2000

#: 読めたか
OK = "ok"
ACCESS = "access"
MISSING = "missing"
UNREACHABLE = "unreachable"
TIMEOUT = "timeout"
ERROR = "error"

#: sqlite3 として開く拡張子
SUFFIXES = (".sqlite3", ".sqlite", ".db")

#: 明細の列(共有の名前 → 集計明細CSVの見出し)。
#: **CSVと同じ字にそろえます** ── 画面も書き出しも同じ組み立てで通せます
DETAIL_MAP: tuple[tuple[str, str], ...] = (
    ("LOT", "ロット№"),
    ("ZAI", "材・調質"),
    ("SIZ", "製品寸法 厚×幅×丈"),
    ("KEN", "検入枚数"),
    ("KZ", "梱包作業時間 開始 時"),
    ("KH", "梱包作業時間 開始 分"),
    ("SZ", "梱包作業時間 終了 時"),
    ("SH", "梱包作業時間 終了 分"),
    ("HIT", "作業人数"),
    ("AI", "合紙"),
    ("MAI", "梱包数量 個装単位 枚数"),
    ("TUT", "梱包数量 梱包単位 包数"),
    ("VC", "ＶＣ種別 両面・片面"),
    ("ET", "etc 反転・EX etc"),
    ("S", "作業停止① 記号"),
    ("TH", "作業停止① 時間(分)"),
    ("SS", "作業停止② 記号"),
    ("THS", "作業停止② 時間(分)"),
    ("STH", "作業停止③ 記号"),
    ("THT", "作業停止③ 時間(分)"),
    ("CON", "実績合計 枚数"),
    ("WEI", "実績合計 重量"),
    ("TIM", "作業時間(分)"),
    ("UNI", "単重"),
    ("Others1", "用途コード"),
    ("Others2", "用途名"),
    ("Others3", "納入先"),
    ("Others4", "包装仕様書No"),
    ("Others5", "コイル縦割"),
    ("Others6", "コイル横縦割"),
)

#: 見出しの表から持ってくるもの(行ごとに繰り返して入れます)
HEADER_MAP: tuple[tuple[str, str], ...] = (
    ("担当者", "作業者"),
    ("昼勤", "昼稼働"),
    ("理由", "作業コメント"),
)

#: `2026年11月17日`。共有の「報告日」はこの書き方です
_JP_DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日")


def parse_report_date(text: str) -> date | None:
    """`2026年11月17日` / `2026-11-17` のどちらも読みます。"""
    trimmed = (text or "").strip()
    found = _JP_DATE.match(trimmed)
    if found:
        try:
            return date(int(found.group(1)), int(found.group(2)),
                        int(found.group(3)))
        except ValueError:
            return None
    return plate_source.parse_date(trimmed)


def as_text(value: object) -> str:
    """共有の値を字に。**`1.0` は `1`** にします(VBAの整数が REAL で入ります)。"""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def is_sqlite(path: Path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


@dataclass
class Readout:
    """読んだ結果。**読めなくても例外にしません**(呼ぶ側は続けます)。"""

    status: str = OK
    path: Path | None = None
    rows: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    detail: str = ""
    seconds: float = SECONDS
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK

    def reason(self) -> str:
        """読まなかった・読めなかったわけ。**「〜ので」まで。**"""
        name = Path(self.path).name if self.path else ""
        if self.status == ACCESS:
            return f"共有の日報データ({name})が Access なので"
        if self.status == MISSING:
            return f"共有の日報データ({name})がまだ無いので"
        if self.status == UNREACHABLE:
            where = Path(self.path).parent if self.path else ""
            return f"共有の日報データの置き場所({where})に届かないので"
        if self.status == TIMEOUT:
            return f"共有の日報データを読むのに {self.seconds:g}秒を超えたので"
        if self.status == ERROR:
            return f"共有の日報データ({name})を読めなかったので"
        return ""


class _TimedOut(Exception):
    """期限までに読み終わらなかった。"""


def _within(seconds: float, work: Callable[[float], None]) -> None:
    """`work(期限)` を別の糸で動かし、`seconds` 秒で見切る。"""
    deadline = time.monotonic() + seconds
    box: dict[str, BaseException] = {}

    def run() -> None:
        try:
            work(deadline)
        except BaseException as exc:              # noqa: BLE001 - 投げ直す
            box["error"] = exc

    worker = threading.Thread(target=run, name="report-db", daemon=True)
    worker.start()
    worker.join(seconds + 0.5)
    if worker.is_alive():
        raise _TimedOut()                         # 置いていきます(進行は止まります)
    if "error" in box:
        raise box["error"]


def _open(path: Path, deadline: float) -> sqlite3.Connection:
    """**読むだけ**で開く(`?mode=ro`)。期限を過ぎたら問い合わせを止めます。"""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                           timeout=max(0.5, deadline - time.monotonic()))
    conn.row_factory = sqlite3.Row
    conn.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0, 2000)
    return conn


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]


def _page_of(row: sqlite3.Row, names: list[str]) -> str:
    for name in PAGE_NAMES:
        if name in names:
            text = as_text(row[name])
            if text:
                return text
    return "1"


def tables(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    r"""ライン名 → (見出しの表, 明細の表)。

    **表の名前からラインを拾います** ── 設定で持つより、置いてあるものを
    見るほうが確かです(ラインが増えても設定を直さずに済みます)。
    """
    found = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    out: dict[str, tuple[str, str]] = {}
    for name in sorted(found):
        if not name.startswith(HEADER_PREFIX):
            continue
        line = name[len(HEADER_PREFIX):]
        detail = f"{DETAIL_PREFIX}{line}"
        if detail in found:
            out[line] = (name, detail)
    return out


def _read(path: Path, seconds: float,
          work: Callable[[sqlite3.Connection], None],
          out: Readout | None = None) -> Readout:
    """共通の関門(Access・無い・届かない・時間切れ・読めない)。

    **読めなかったときは、読みかけの中身を返しません** ── 途中まで入った
    行を「全部」として出すと、少ない行を見て「無い」と判じてしまいます。
    """
    out = out or Readout(path=path, seconds=seconds)
    out.path, out.seconds = path, seconds
    if not is_sqlite(path):
        out.status = ACCESS
        return out

    def run(deadline: float) -> None:
        try:
            exists = path.is_file()
            reachable = exists or path.parent.is_dir()
        except OSError:
            exists = reachable = False
        if not exists:
            out.status = MISSING if reachable else UNREACHABLE
            return
        conn = _open(path, deadline)
        try:
            work(conn)
        finally:
            conn.close()
        out.status = OK

    try:
        _within(seconds, run)
    except _TimedOut:
        log.warning("共有の日報データを %s秒で読み終われませんでした: %s",
                    seconds, path)
        return Readout(status=TIMEOUT, path=path, seconds=seconds)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            return Readout(status=TIMEOUT, path=path, seconds=seconds)
        log.warning("共有の日報データを読めませんでした %s: %s", path, exc)
        return Readout(status=ERROR, path=path, detail=str(exc), seconds=seconds)
    except Exception as exc:                      # noqa: BLE001 - 手元で続ける
        log.warning("共有の日報データを読めませんでした %s: %s", path, exc)
        return Readout(status=ERROR, path=path, detail=str(exc), seconds=seconds)
    return out


def state(path: Path) -> Readout:
    """開けるかどうかと、置いてあるラインだけ見る(設定タブの札に出します)。"""
    out = Readout(path=Path(path))

    def work(conn: sqlite3.Connection) -> None:
        out.lines = sorted(tables(conn))

    return _read(Path(path), SECONDS, work, out)


def lot_rows(path: Path, lines: list[str], start: date, end: date,
             match: Callable[[str], bool], *, seconds: float | None = None,
             cap: int | None = None) -> Readout:
    r"""期間と**ロット№の当たり方**(呼ぶ側が渡す)で、明細の行を読む。

    当たり方を関数で渡すのは、全角・小文字を揃えてから頭を比べるためです
    ── SQL の `LIKE` では書けません。SQLの中から呼ぶので、**当たらない行は
    持ってきません**(`create_function`)。

    `lines` が空なら、置いてある表のライン全部です。
    """
    path = Path(path)
    # **既定は呼ぶときに決めます**(引数の既定値にすると、読み込んだ
    # ときの値で固まり、あとから変えられません)
    seconds = SECONDS if seconds is None else seconds
    cap = CAP if cap is None else cap

    def in_span(text: object) -> int:
        day = parse_report_date(as_text(text))
        return 1 if day is not None and start <= day <= end else 0

    def hit(text: object) -> int:
        try:
            return 1 if match(as_text(text)) else 0
        except Exception:                         # noqa: BLE001 - 比べられない字
            return 0

    out = Readout(path=path, seconds=seconds)

    def work(conn: sqlite3.Connection) -> None:
        conn.create_function("in_span", 1, in_span, deterministic=True)
        conn.create_function("lot_hit", 1, hit, deterministic=True)
        found = tables(conn)
        out.lines = sorted(found)
        wanted = [name for name in sorted(found) if not lines or name in lines]
        for line in wanted:
            header_table, detail_table = found[line]
            head_names = _columns(conn, header_table)
            heads: dict[tuple[str, str], dict[str, str]] = {}
            for row in conn.execute(f"SELECT * FROM {_quote(header_table)}"
                                    ' WHERE in_span("報告日")'):
                key = (as_text(row["直"]), _page_of(row, head_names))
                stamp = as_text(row["報告日"])
                heads[(stamp, *key)] = {
                    label: as_text(row[name]) if name in head_names else ""
                    for name, label in HEADER_MAP}
            if not heads:
                continue
            detail_names = _columns(conn, detail_table)
            left = cap + 1 - len(out.rows)
            for row in conn.execute(
                    f"SELECT * FROM {_quote(detail_table)}"
                    ' WHERE in_span("報告日") AND lot_hit("LOT") LIMIT ?',
                    (left,)):
                stamp = as_text(row["報告日"])
                day = parse_report_date(stamp)
                shift = as_text(row["直"])
                page = _page_of(row, detail_names)
                head = heads.get((stamp, shift, page))
                if head is None:
                    continue                      # 見出しの無い明細は紙の鍵にならない
                one = {
                    "日付": plate_source.date_text(day) if day else stamp,
                    "ライン": as_text(row["ライン"]) or line,
                    "直": shift, "ページ": page,
                    "行": as_text(row["行番号"]),
                }
                for name, label in DETAIL_MAP:
                    if name in detail_names:
                        one[label] = as_text(row[name])
                one.update(head)
                out.rows.append(one)
            if len(out.rows) > cap:
                out.truncated = True
                del out.rows[cap:]
                return

    done = _read(path, seconds, work, out)
    if done.ok:
        log.info("共有の日報データから %d 行(ライン %s)", len(done.rows),
                 "／".join(done.lines) or "(なし)")
    return done
