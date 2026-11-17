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
SHEET_AGGREGATE = "集計"
SHEET_STOP = "停止内訳"
SHEET_STOP_TOTAL = "停止集計"


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


def _charts(sheet: xlsx_out.Sheet, columns: list[str]) -> list[xlsx_out.Chart]:
    """集計シートの列から、グラフを組む。**無い列は飛ばします。**

    all-tools は列を足します(`その他(分)`)。位置ではなく**名前で**
    探すので、足されても減っても組める範囲で組みます。
    """
    def column_of(name: str) -> int:
        return columns.index(name) if name in columns else -1

    date_at = column_of("日付")
    if date_at < 0:
        return []
    charts: list[xlsx_out.Chart] = []

    def add(title: str, pairs: list[tuple[str, str]], y_title: str) -> None:
        series = [xlsx_out.Series(title=label, column=column_of(name),
                                  kind=("bar" if name == "合計枚数" else "line"))
                  for label, name in pairs if column_of(name) >= 0]
        if series:
            charts.append(xlsx_out.Chart(title=title, source=sheet.name,
                                         labels=date_at, series=series,
                                         y_title=y_title))

    add("日別・直別の重量", [("合計重量(t)", "合計重量(t)")], "t")
    add("日別・直別の枚数", [("合計枚数", "合計枚数")], "枚")
    add("稼働率と生産性", [("稼働率(%)", "稼働率(%)"),
                           ("生産性(t/h)", "生産性(t/h)")], "%  /  t/h")
    add("停止の分類ごと", [(label, column)
                           for column, label in plate_graph.STOP_KIND_COLUMNS]
        + [("停止合計(分)", "停止合計(分)")], "分")
    return charts


def write_xlsx(collected: plate_source.Collected, path: Path,
               notes: list[str]) -> Path:
    """Excel を1つ書く(**グラフ付き**)。"""
    path = Path(path)
    agg = _sheet(SHEET_AGGREGATE, collected.columns, collected.rows, notes)
    sheets = [agg]
    if collected.stops:
        sheets.append(_sheet(SHEET_STOP, collected.stop_columns,
                             collected.stops, []))
        table = plate_graph.stop_table_tile(collected.stops)
        sheets.append(xlsx_out.Sheet(
            name=SHEET_STOP_TOTAL, columns=table.columns,
            rows=[[_value(cell) for cell in row] for row in table.rows],
            widths={0: 18, 1: 22}))
    xlsx_out.write(path, sheets, _charts(agg, collected.columns))
    log.info("板集計のExcelを書きました: %s (%d 行・グラフ %d 枚)",
             path, len(collected.rows), len(_charts(agg, collected.columns)))
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
