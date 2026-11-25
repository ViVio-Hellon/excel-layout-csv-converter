r"""板集計の書き出し ── CSV 1本、または Excel(グラフ付き)

**ダウンロードはしません。** 設定部の出力先フォルダへ、この端末の Python が
直接書きます(このリポジトリの約束)。

    板集計_L-1_2026-11-01-2026-11-30.csv     まとめた集計(1本)
    板集計_L-1_2026-11-01-2026-11-30.xlsx    集計・停止内訳・**グラフ**

【CSV のときグラフは付けません】
CSV は表だけの形です。グラフが要るなら Excel を選んでください ──
画面(板集計タブ)でも同じグラフが見られます。

【Excel に何を入れるか】
    グラフ    … 日別の推移(重量・枚数・稼働率)・直別・停止の分類
    集計      … まとめた集計(グラフはこのセルを指しています)
    停止内訳  … 期間ぶんの停止(分類・内訳つき)
    停止集計  … 内訳ごとの合計(長い順)。輪に出なかったものも全部

グラフが**集計シートのセルを指す**ので、期間を絞ったり系列を足したりは
受け取った人が Excel でできます(画像で貼ると、そこで手が止まります)。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from . import plate_graph, plate_source, xlsx_out
from .logging_utils import get_logger

log = get_logger("plate")

#: 出すファイルの頭
PREFIX = "板集計"

#: シートの名前。**グラフはこの名前で表を指します**
SHEET_DAILY = "集計"                   # 1行=1日(直は列。VBAのブックと同じ)
SHEET_STOP_TOTAL = "停止集計"          # 分類ごとの内訳(円グラフの材料)
SHEET_STOP = "停止内訳"                # 読んだままの停止
SHEET_SOURCE = "元データ"              # CSVで出すのと同じ行(直ごと)
#: 前の名前(呼んでいる所が残っていないかを試験で見ます)
SHEET_AGGREGATE = SHEET_DAILY


def file_stem(lines: list[str], start: str, end: str) -> str:
    r"""`板集計_L-1_2026-11-01-2026-11-30`。

    **ラインを名前に入れます。** 共有の出力先へ何本も溜まるので、
    開かずに「どのラインの、いつからいつまで」が分かる必要があります。
    ラインが多いときは数にします(名前が長すぎると Windows が断ります)。
    """
    if len(lines) == 1:
        label = lines[0]
    elif len(lines) <= 3:
        label = "-".join(lines)
    else:
        label = f"{len(lines)}ライン"
    span = start if start == end else f"{start}-{end}"
    # 名前に使えない字を外します(ライン名は現場の呼び名そのままなので、
    # `/` や `:` が入っていることがあります)
    stem = re.sub(r'[\\/:*?"<>|]+', "_", f"{PREFIX}_{label}_{span}").strip()
    return stem[:120] or PREFIX


def write_csv(collected: plate_source.Collected, path: Path) -> Path:
    """まとめた集計を**1本の CSV**にする。

    BOM付きUTF-8で書きます ── Windows の Excel がそのまま開けます
    (このリポジトリのほかのCSVと同じ書き方)。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=collected.columns,
                                extrasaction="ignore")
        writer.writeheader()
        for row in collected.rows:
            writer.writerow({name: row.get(name, "")
                             for name in collected.columns})
    log.info("板集計のCSVを書きました: %s (%d 行)", path, len(collected.rows))
    return path


def _value(text: str) -> object:
    """CSVの字を、**数なら数のまま**シートへ入れる。

    数を字で入れるとグラフが描けず、合計も並べ替えもできません
    (`xlsx_out.Sheet` の説明)。日付のような字はそのまま入れます。
    """
    trimmed = (text or "").strip()
    if not trimmed:
        return ""
    cleaned = trimmed.replace(",", "")
    try:
        number = float(cleaned)
    except ValueError:
        return trimmed
    if cleaned.lstrip("-").isdigit():
        return int(cleaned)
    return number


def _sheet(name: str, columns: list[str], rows: list[dict[str, str]],
           notes: list[str]) -> xlsx_out.Sheet:
    body = [[_value(row.get(column, "")) for column in columns]
            for row in rows]
    widths = {0: 12, 1: 10}
    for index, column in enumerate(columns):
        if len(column) >= 10:            # 長い見出しは幅を取る(切れると読めない)
            widths[index] = min(22, len(column) + 4)
    return xlsx_out.Sheet(name=name, columns=list(columns), rows=body,
                          widths=widths, notes=notes)


#: 日別シートの列。**VBAが出していた月別集計のブックに合わせます**
#: (`docs/板集計.md` の「Excelの作り」)。向こうは
#: `日付 / 合計(枚) / 合計(t) / 1直合計(枚) / 1直合計(t) / … /
#:  作業時間合計(分) / 稼働時間合計(分) / 管理ロス停止(分) / 突発停止(分) /
#:  ﾊﾝﾄﾞﾘﾝｸﾞ停止(分) / 稼働率(％) / 生産性1直(t/h) / … / 日毎累計(枚)`
#: という並びで、**1行が1日**です ── 直は行ではなく**列**で分かれます。
#: グラフが「直別」になるのはこの形だからで、直を行に持ったままでは
#: 系列が分けられません(そこが食い違っていました)。
COL_DATE = "日付"
COL_LINE = "ライン"
COL_SHEETS = "合計(枚)"
COL_WEIGHT = "合計(t)"
COL_WORK = "作業時間合計(分)"
COL_OPERATING = "稼働時間合計(分)"
COL_STOP = "停止合計(分)"
COL_RATE = "稼働率(％)"
COL_CUM_SHEETS = "日毎累計(枚)"
COL_CUM_WEIGHT = "日毎累計(t)"

#: 停止の分類の列(日別シート)。名前はVBAのブックに寄せます
STOP_COLUMNS_OUT = (
    ("管理ロス設備停止", "管理ロス停止(分)"),
    ("段取り・突発停止", "突発停止(分)"),
    ("ハンドリング停止", "ﾊﾝﾄﾞﾘﾝｸﾞ停止(分)"),
    ("その他", "その他停止(分)"),
)


def _daily_sheet(collected: plate_source.Collected,
                 notes: list[str]) -> xlsx_out.Sheet:
    r"""**1行=1日**の集計シート(直は列で分かれます)。

    ラインを2つ以上選んだときは「1行 = 1日 × 1ライン」にして、ライン名の
    列を足します ── 混ぜて足すと、どのラインの直かが分からなくなります。

    累計(`日毎累計`)は**ラインごと**に積みます。
    """
    rows = plate_graph.shift_rows(collected.rows)
    shifts = sorted({r.shift for r in rows if r.shift},
                    key=plate_graph.shift_sort_key)
    many = len(collected.lines) > 1

    columns = [COL_DATE] + ([COL_LINE] if many else []) \
        + [COL_SHEETS, COL_WEIGHT]
    for shift in shifts:
        columns += [f"{shift}合計(枚)", f"{shift}合計(t)"]
    columns += [COL_WORK, COL_OPERATING]
    columns += [label for _, label in STOP_COLUMNS_OUT]
    columns += [COL_STOP, COL_RATE]
    columns += [f"生産性{shift}(t/h)" for shift in shifts]
    columns += [COL_CUM_SHEETS, COL_CUM_WEIGHT]

    #: (日付, ライン) → その日の直の行
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault((row.date, row.line if many else ""), []).append(row)

    running: dict[str, list[float]] = {}
    body: list[list[object]] = []
    # **日付として**並べます(字の順だと 11月1日 が 11月19日 の後ろへ)
    for (day, line) in sorted(groups, key=lambda key: (
            plate_source.sort_date(key[0]), key[1])):
        same = groups[(day, line)]
        total_sheets = sum(r.sheets for r in same)
        total_weight = sum(r.weight for r in same)
        keep = running.setdefault(line, [0.0, 0.0])
        keep[0] += total_sheets
        keep[1] += total_weight
        values: list[object] = [day] + ([line] if many else []) \
            + [round(total_sheets), round(total_weight, 2)]
        for shift in shifts:
            picked = [r for r in same if r.shift == shift]
            values += [round(sum(r.sheets for r in picked)),
                       round(sum(r.weight for r in picked), 2)]
        values += [round(sum(r.work for r in same)),
                   round(sum(r.operating for r in same))]
        for label, _ in STOP_COLUMNS_OUT:
            values.append(round(sum(r.kinds.get(label, 0.0) for r in same)))
        values += [round(sum(r.stop for r in same)),
                   round(plate_graph.day_rate_pct(same), 1)]
        for shift in shifts:
            picked = [r for r in same if r.shift == shift]
            # 生産性は直ごとに持っている数をそのまま(平均しません ──
            # 1日1直なら1つ、同じ直が2行あるときだけ足して割ります)
            values.append(round(sum(r.productivity for r in picked)
                                / len(picked), 2) if picked else 0)
        values += [round(keep[0]), round(keep[1], 2)]
        body.append(values)

    widths = {0: 14}
    for index, name in enumerate(columns):
        if len(name) >= 8:
            widths[index] = min(20, len(name) + 3)
    return xlsx_out.Sheet(name=SHEET_DAILY, columns=columns, rows=body,
                          widths=widths, notes=notes)


def _stop_pie_sheet(stops: list[dict[str, str]]) -> tuple[xlsx_out.Sheet, list]:
    """**分類ごと**に並べた停止の表と、その行の範囲(円グラフが見ます)。

    VBAのブックは分類ごとに列を分けていましたが、ここは1つの表に
    `分類 / 内訳 / 回数 / (分)` で並べ、**円グラフが行の範囲を指します** ──
    分類が増えても列を足さずに済みます。
    """
    totals: dict[tuple[str, str], list[float]] = {}
    for row in stops:
        if row.get("区分") and row.get("区分") != "直の合計":
            continue                      # 1件ずつの行を足すと二重になります
        name = (row.get("内訳") or row.get("記号") or "").strip()
        if not name:
            continue
        key = (row.get("分類", "") or "(分類なし)", name)
        found = totals.setdefault(key, [0.0, 0.0])
        found[0] += plate_source.number(row.get("停止時間(分)"))
        found[1] += plate_source.number(row.get("回数")) or 1

    body: list[list[object]] = []
    spans: list[tuple[str, int, int]] = []    # (分類, 先頭行, 最終行)
    for kind in sorted({k for k, _ in totals}):
        picked = sorted(((name, value) for (k, name), value in totals.items()
                         if k == kind), key=lambda one: -one[1][0])
        first = len(body) + 1                 # 表の中の番号(1から)
        for name, value in picked:
            body.append([kind, name, round(value[1]), round(value[0])])
        spans.append((kind, first, len(body)))
    sheet = xlsx_out.Sheet(name=SHEET_STOP_TOTAL,
                           columns=["分類", "内訳", "回数", "(分)"],
                           rows=body, widths={0: 18, 1: 24})
    return sheet, spans


def _charts(daily: xlsx_out.Sheet, stops: xlsx_out.Sheet,
            spans: list) -> list[xlsx_out.Chart]:
    r"""グラフを組む。**VBAのブックと同じ並び**です。

        直重量(t)             棒。**直ごとに系列を分ける**
        直枚数(枚)            棒。同上
        停止・稼働時間(分)     棒。作業・稼働と停止の分類
        生産性(t/h)           棒。直ごと(稼働率とは**別のグラフ**)
        稼働率(％)            折れ線。%は桁が違うので混ぜません
        累積枚数(枚)          折れ線
        直枚数・累積枚数推移   棒 + 折れ線。**累積は右の目盛り**
        停止の分類ごと         **円**(分類の数だけ)

    稼働率と生産性を1枚に重ねていたのをやめました ── 90 と 3 を同じ
    目盛りに乗せると、生産性が床に貼り付いて折れ線に見えません。
    """
    columns = daily.columns

    def at(name: str) -> int:
        return columns.index(name) if name in columns else -1

    date_at = at(COL_DATE)
    if date_at < 0:
        return []
    shifts = [name[:-len("合計(枚)")] for name in columns
              if name.endswith("合計(枚)") and name != COL_SHEETS]
    charts: list[xlsx_out.Chart] = []

    def add(title: str, pairs: list[tuple[str, str, str]], y_title: str,
            secondary: tuple[str, str] | None = None) -> None:
        series = [xlsx_out.Series(title=label, column=at(name), kind=kind)
                  for label, name, kind in pairs if at(name) >= 0]
        if secondary and at(secondary[0]) >= 0:
            series.append(xlsx_out.Series(title=secondary[1],
                                          column=at(secondary[0]),
                                          kind="line", axis="secondary"))
        if series:
            charts.append(xlsx_out.Chart(title=title, source=daily.name,
                                         labels=date_at, series=series,
                                         y_title=y_title))

    add("直重量(t)", [(f"{s}(t)", f"{s}合計(t)", "bar") for s in shifts], "t")
    add("直枚数(枚)", [(f"{s}(枚)", f"{s}合計(枚)", "bar") for s in shifts], "枚")
    add("停止・稼働時間(分)",
        [("作業時間合計", COL_WORK, "bar"), ("稼働時間合計", COL_OPERATING, "bar")]
        + [(label, label, "bar") for _, label in STOP_COLUMNS_OUT], "分")
    add("生産性(t/h)",
        [(s, f"生産性{s}(t/h)", "bar") for s in shifts], "t/h")
    add("稼働率(％)", [("稼働率", COL_RATE, "line")], "％")
    add("累積枚数(枚)", [("日毎累計", COL_CUM_SHEETS, "line")], "枚")
    # **桁が2つ違うので、累積だけ右の目盛り**(Excelの「第2軸に移動」と同じ)
    add("直枚数・累積枚数推移",
        [(f"{s}(枚)", f"{s}合計(枚)", "bar") for s in shifts], "枚",
        secondary=(COL_CUM_SHEETS, "累積枚数(右目盛り)"))

    for kind, first, last in spans:
        if last < first:
            continue
        charts.append(xlsx_out.Chart(
            title=f"「{kind}」(分)", source=stops.name, kind="pie",
            labels=1,                          # 内訳の列
            series=[xlsx_out.Series(title="(分)", column=3, kind="pie")],
            row_from=stops.first_data_row + first - 1,
            row_to=stops.first_data_row + last - 1))
    return charts


def write_xlsx(collected: plate_source.Collected, path: Path,
               notes: list[str]) -> Path:
    r"""Excel を1つ書く(**グラフ付き**)。

    シートの並びは「絵 → 日ごとの数 → 停止 → 元のまま」です。
    """
    path = Path(path)
    daily = _daily_sheet(collected, notes)
    stops, spans = _stop_pie_sheet(collected.stops)
    sheets = [daily, stops]
    if collected.stops:
        sheets.append(_sheet(SHEET_STOP, collected.stop_columns,
                             collected.stops, []))
    # **元のまま**も残します(CSVで出したものと同じ行)── 数が合わない
    # ときに、どこで変わったのかを追えるように
    sheets.append(_sheet(SHEET_SOURCE, collected.columns, collected.rows, []))
    charts = _charts(daily, stops, spans)
    xlsx_out.write(path, sheets, charts)
    log.info("板集計のExcelを書きました: %s (%d 日・グラフ %d 枚)",
             path, len(daily.rows), len(charts))
    return path


def notes_of(collected: plate_source.Collected, start: str, end: str) -> list[str]:
    """シートの頭に出す覚え書き。**読めなかったものも書きます。**

    「出たCSVの行が思ったより少ない」ときに、いちばん先に見る場所です。
    """
    found = [f"板集計  期間 {start} 〜 {end}",
             f"ライン: {'／'.join(collected.lines) or '(なし)'}"
             f"   日数: {len(collected.dates)}   行: {len(collected.rows)}"
             f"   読んだCSV: {collected.files}"]
    if collected.skipped:
        found.append(f"読めなかったもの: {len(collected.skipped)} 件"
                     "(下に理由)")
        for one in collected.skipped[:20]:
            found.append(f"  ・{one.get('line', '')} {one.get('date', '')}"
                         f" {one.get('reason', '')}")
        if len(collected.skipped) > 20:
            found.append(f"  ・ほか {len(collected.skipped) - 20} 件")
    return found


def write(collected: plate_source.Collected, out_dir: Path, *,
          kind: str, start: str, end: str) -> dict:
    """CSV か Excel を1つ書いて、**何をどこへ書いたか**を返す。"""
    out_dir = Path(out_dir)
    stem = file_stem(collected.lines or ["なし"], start, end)
    if kind == "xlsx":
        path = write_xlsx(collected, out_dir / f"{stem}.xlsx",
                          notes_of(collected, start, end))
    else:
        path = write_csv(collected, out_dir / f"{stem}.csv")
    return {"path": str(path), "name": path.name, "kind": kind,
            "rows": len(collected.rows), "stops": len(collected.stops),
            "days": len(collected.dates), "lines": collected.lines,
            "skipped": collected.skipped}
