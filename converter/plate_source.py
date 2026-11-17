r"""板データの参照 ── 日ごとのCSVを**期間ぶん1つに**まとめる

【どこを読むか】
指された参照パスの下は、日報管理ツール(all-tools)が書いた形です
(`nippou/reporting/csv_export.py` の `line_dir` / `dated_dir`)。
**このツールの変換タブが出す4本組も同じ形**なので、どちらも読めます。

    <参照パス>\L-1\集計\2026.11\17\集計_2026-11-17_L-1.csv
                                    集計明細_2026-11-17_L-1.csv
                                    停止内訳_2026-11-17_L-1.csv
              L-1\GW計算記録\…
              L-1\印刷\…            ← 紙(HTML)。ここでは読みません

上から **ライン → 種類(集計) → 年月 → 日**。探すときに絞る順です。

【何を1本にするか】
**集計CSV(直ごとの合計)を、期間ぶん縦に連結します。** 1日3〜4行なので
ひと月で100行ほど ── 表にしてもグラフにしても扱える大きさで、
日報管理ツールのグラフと**同じ数**になります(どちらも集計が出どころ)。

停止内訳CSVは**グラフの材料**として別に読みます(分類の輪・項目ごとの輪)。
Excelへ出すときは別のシートに載せます。集計明細は読みません ──
1日12行×直×日数で、まとめる相手ではありません(必要なら機側データ集計タブ)。

【列は名前で読みます。位置では読みません】
集計CSVの列は増えます(all-tools は `その他(分)` を足しました)。位置で
読むと、足された日から**静かに1列ずれます**。見出しの名前で拾い、
知らない列は**そのまま後ろへ付けて**出します ── 読めない列を捨てると、
せっかく増えた欄が消えます。

【読めなかったものは黙って捨てません】
日のフォルダがあるのにCSVが無い、見出しが違う、文字が壊れている ──
どれも「その日が抜けたグラフ」になります。理由を数えて画面へ返します。
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .logging_utils import get_logger

log = get_logger("plate")

#: ラインフォルダの下の種類。日報管理ツールの `csv_export.KIND_AGGREGATE` と同じ字
KIND_AGGREGATE = "集計"

#: 日ごとのCSVの頭。`集計_` は `集計明細_` に当たりません(区切りの `_` が入るため)
PREFIX_AGGREGATE = "集計_"
PREFIX_STOP = "停止内訳_"

#: 年月のフォルダ(`2026.11`)と日のフォルダ(`17`)
MONTH_DIR = re.compile(r"^(\d{4})\.(\d{2})$")
DAY_DIR = re.compile(r"^(\d{1,2})$")

#: 集計CSVの列。**この並びで出します**(知らない列は後ろへ)。
#: 出どころは `nippou.reporting.csv_export.FIELDNAMES` ですが、
#: **写しの版に縛られないよう**ここに書きます ── 向こうは列が増えます
AGGREGATE_COLUMNS = (
    "日付", "ライン", "直",
    "合計枚数", "合計重量(t)",
    "作業時間合計(分)", "稼働時間合計(分)", "操業時間(分)",
    "管理ロス設備停止(分)", "段取り・突発停止(分)", "ハンドリング停止(分)",
    "その他(分)", "停止合計(分)",
    "稼働率(%)", "生産性(t/h)",
)

#: 停止内訳CSVの列(同じ考え方)
STOP_COLUMNS = (
    "日付", "ライン", "直", "区分", "分類", "記号", "内訳",
    "ページ", "行", "LOTNO", "停止の位置",
    "回数", "停止時間(分)", "その直の停止に占める割合(%)", "休憩の時刻",
)

#: これが無ければ集計CSVとみなしません(**別のCSVを読み込まないため**)
AGGREGATE_REQUIRED = ("日付", "ライン", "直")

#: 一度に読む日数の上限。**止まらないため**の歯止めです
#: (1日3本×366日 ≒ 1100ファイル。これ以上はふつう範囲の指定間違い)
MAX_DAYS = 400


# ----------------------------------------------------------------------
# 日付の読み方
# ----------------------------------------------------------------------
def parse_date(text: str) -> date | None:
    r"""`2026-11-17` / `2026/11/17` / `2026.11.17` を受けます。

    CSVの中の「日付」列は書き手によって区切りが違います(VBA から
    引き継いだものは `.`)。**読めない1行で日が飛ぶ**より受けます。
    """
    trimmed = (text or "").strip()
    if not trimmed:
        return None
    parts = re.split(r"[-/.]", trimmed)
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def date_text(value: date) -> str:
    """画面と出力で使う書き方(`2026-11-17`)。CSVの中の字と同じにします。"""
    return value.isoformat()


# ----------------------------------------------------------------------
# 参照パスを見る
# ----------------------------------------------------------------------
@dataclass
class LineFound:
    """ラインフォルダ1つぶん。**画面の一覧に出すもの。**"""

    name: str
    #: 集計フォルダがあるか。無ければ読む相手が無い(紙だけのライン)
    has_aggregate: bool = False
    days: int = 0
    first: str = ""
    last: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "has_aggregate": self.has_aggregate,
                "days": self.days, "first": self.first, "last": self.last}


def lines(base: Path) -> list[LineFound]:
    r"""参照パス直下のラインフォルダを並べる(`L-1` `LS` …)。

    **中を見ないと分かりません。** フォルダ名からはラインかどうか
    決められないので(`配布設定` のような持ちものも並びます)、
    `集計` フォルダを持っているかで見分けます ── 無いものも一覧には
    出して「集計がありません」と言います。黙って消すと、名前を
    打ち間違えたのか、置き場所が違うのかが分かりません。
    """
    found: list[LineFound] = []
    try:
        children = sorted((p for p in base.iterdir() if p.is_dir()),
                          key=lambda p: p.name)
    except OSError as exc:
        log.warning("板データ参照パスを開けません: %s (%s)", base, exc)
        return found
    for child in children:
        one = LineFound(name=child.name)
        agg = child / KIND_AGGREGATE
        one.has_aggregate = agg.is_dir()
        if one.has_aggregate:
            dates = [d for d, _ in days(base, child.name)]
            one.days = len(dates)
            if dates:
                one.first = date_text(dates[0])
                one.last = date_text(dates[-1])
        found.append(one)
    return found


def days(base: Path, line: str) -> list[tuple[date, Path]]:
    r"""そのラインの日フォルダを**日付順**に並べる。

    フォルダの名前から日付を作ります(`2026.11\17` → 2026-11-17)──
    中のCSVを開かずに期間で絞れるので、ひと月を指しても1年ぶんの
    CSVを読みません。
    """
    root = base / line / KIND_AGGREGATE
    found: list[tuple[date, Path]] = []
    if not root.is_dir():
        return found
    try:
        months = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for month in months:
        matched = MONTH_DIR.match(month.name)
        if not matched:
            continue
        year, mon = int(matched.group(1)), int(matched.group(2))
        try:
            children = sorted(p for p in month.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in children:
            if not DAY_DIR.match(folder.name):
                continue
            try:
                found.append((date(year, mon, int(folder.name)), folder))
            except ValueError:            # 2月30日のようなフォルダ名
                continue
    found.sort(key=lambda pair: pair[0])
    return found


# ----------------------------------------------------------------------
# CSVを読む
# ----------------------------------------------------------------------
def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str], str]:
    """1本読む。`(行, 見出し, うまくいかなかった理由)` を返します。

    **BOM付きUTF-8で書かれています**(Excelでそのまま開けるように)。
    `utf-8-sig` で開くと、BOM の有無どちらでも読めます。文字が壊れて
    いる1字でCSV全体を落とさないよう、読めない字は置き換えます ──
    数字と日付が読めれば集計はできます。
    """
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace",
                       newline="") as handle:
            reader = csv.DictReader(handle)
            header = [h for h in (reader.fieldnames or []) if h]
            rows = [{k: (v or "") for k, v in row.items() if k}
                    for row in reader]
    except FileNotFoundError:
        return [], [], "ありません"
    except OSError as exc:
        return [], [], f"読めません({exc.strerror or exc})"
    return rows, header, ""


def find_csv(folder: Path, prefix: str) -> Path | None:
    """日フォルダの中から、頭の字で1本選ぶ。

    名前にはラインと日付が入ります(`集計_2026-11-17_L-1.csv`)。
    同じ日に2本あることはありませんが、**名前順でいちばん後ろ**を
    取ります ── 書き直したものが残っていたら新しいほうです。
    """
    try:
        found = sorted(p for p in folder.iterdir()
                       if p.is_file() and p.name.startswith(prefix)
                       and p.suffix.lower() == ".csv")
    except OSError:
        return None
    return found[-1] if found else None


# ----------------------------------------------------------------------
# 期間ぶんまとめる
# ----------------------------------------------------------------------
@dataclass
class Collected:
    """まとめた結果。**何を読んで、何を読めなかったかまで持ちます。**"""

    #: 集計CSVの行(期間ぶん縦に連結。日付 → ライン → 直 の順)
    rows: list[dict[str, str]] = field(default_factory=list)
    #: 出すときの列の並び(既知の列 → 見つかった知らない列)
    columns: list[str] = field(default_factory=list)
    #: 停止内訳の行(グラフの材料)
    stops: list[dict[str, str]] = field(default_factory=list)
    stop_columns: list[str] = field(default_factory=list)
    #: 読めた日(日付の字)と、読んだライン
    dates: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    #: 読めなかったもの(日付・ライン・理由)。**画面に出します**
    skipped: list[dict[str, str]] = field(default_factory=list)
    files: int = 0

    @property
    def empty(self) -> bool:
        return not self.rows

    def as_dict(self) -> dict:
        return {"rows": len(self.rows), "stops": len(self.stops),
                "dates": self.dates, "lines": self.lines,
                "files": self.files, "skipped": self.skipped}


def _order_columns(known: tuple[str, ...], seen: list[str]) -> list[str]:
    """既知の列を決めた並びで、知らない列は**見つけた順で後ろへ**。"""
    extra = [name for name in seen if name not in known]
    return [name for name in known if name in seen] + extra


def collect(base: Path, chosen: list[str], start: date, end: date,
            *, with_stops: bool = True) -> Collected:
    r"""期間とラインを決めて、集計CSVを1つにまとめる。

    **読む順は日付 → ライン**です。同じ日の複数ラインが隣に並ぶので、
    1本のCSVにしたときに日ごとの比較ができます。

    `start` > `end` でも受けます(入れ替えます)── 打ち間違いで
    「0件でした」とだけ出るより、意図どおりに動くほうが親切です。
    """
    if start > end:
        start, end = end, start
    result = Collected()
    seen_cols: list[str] = []
    seen_stop_cols: list[str] = []
    dates: set[str] = set()
    used_lines: list[str] = []

    for line in chosen:
        picked = [(day, folder) for day, folder in days(base, line)
                  if start <= day <= end]
        if not picked:
            result.skipped.append({"line": line, "date": "",
                                   "reason": "その期間の日フォルダがありません"})
            continue
        if len(picked) > MAX_DAYS:
            raise ValueError(f"{line}: 期間が広すぎます"
                             f"({len(picked)} 日。一度に {MAX_DAYS} 日までです)")
        used_lines.append(line)
        for day, folder in picked:
            stamp = date_text(day)
            path = find_csv(folder, PREFIX_AGGREGATE)
            if path is None:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": "集計CSVがありません"})
                continue
            rows, header, problem = read_csv(path)
            result.files += 1
            if problem:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": f"集計CSVが{problem}"})
                continue
            if not all(name in header for name in AGGREGATE_REQUIRED):
                result.skipped.append({
                    "line": line, "date": stamp,
                    "reason": "集計CSVの見出しが違います"
                              f"({'・'.join(AGGREGATE_REQUIRED)} がありません)"})
                continue
            for name in header:
                if name not in seen_cols:
                    seen_cols.append(name)
            kept = 0
            for row in rows:
                # **フォルダの日付を信じます。** 行の日付が空でも、
                # どの日のものかはフォルダで決まっています
                if not row.get("日付"):
                    row["日付"] = stamp
                if not row.get("ライン"):
                    row["ライン"] = line
                result.rows.append(row)
                kept += 1
            if kept:
                dates.add(stamp)
            else:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": "集計CSVに行がありません"})

            if not with_stops:
                continue
            stop_path = find_csv(folder, PREFIX_STOP)
            if stop_path is None:
                continue                  # 停止が無い日もあります(止めていない)
            stop_rows, stop_header, stop_problem = read_csv(stop_path)
            result.files += 1
            if stop_problem:
                result.skipped.append({"line": line, "date": stamp,
                                       "reason": f"停止内訳CSVが{stop_problem}"})
                continue
            for name in stop_header:
                if name not in seen_stop_cols:
                    seen_stop_cols.append(name)
            for row in stop_rows:
                if not row.get("日付"):
                    row["日付"] = stamp
                if not row.get("ライン"):
                    row["ライン"] = line
                result.stops.append(row)

    result.rows.sort(key=lambda row: (row.get("日付", ""), row.get("ライン", ""),
                                      row.get("直", "")))
    result.stops.sort(key=lambda row: (row.get("日付", ""), row.get("ライン", ""),
                                       row.get("直", "")))
    result.columns = _order_columns(AGGREGATE_COLUMNS, seen_cols)
    result.stop_columns = _order_columns(STOP_COLUMNS, seen_stop_cols)
    result.dates = sorted(dates)
    result.lines = used_lines
    log.info("板集計: ライン=%s 期間=%s〜%s 日数=%d 行=%d 停止=%d 読めず=%d",
             "／".join(chosen) or "(なし)", date_text(start), date_text(end),
             len(result.dates), len(result.rows), len(result.stops),
             len(result.skipped))
    return result


def number(value: object) -> float:
    """CSVの数の欄を数にする。**空欄と読めない字は 0** にします。

    グラフと合計のためのものです ── 1つ読めない欄があるだけで
    その日が消えるより、0 として足すほうが見える形になります。
    """
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0
