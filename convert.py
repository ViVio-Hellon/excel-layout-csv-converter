#!/usr/bin/env python3
"""エクセル(日付シート) → CSV 変換器

日報管理ツールと**同じCSV**を書きます。画面もサーバも常設のDBも要りません。

    python convert.py <ファイル or フォルダ> ... --out <出力先>

    python convert.py 2026.08 --out C:\\出力              フォルダごと
    python convert.py a.xlsx b.xlsx --out out --line LS    ラインを指定
    python convert.py 2026.08 --check                     読めるかだけ見る
    python convert.py 2026.08 --out out --tally           機側データ集計も出す
    python convert.py 2026.08 --out out --ref \\\\共有\\参照  停止内訳マスタの場所

**追加ライブラリは要りません**(Python 3.11 以上の標準ライブラリだけ)。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 写してある日報管理ツールの中身。**同じコードを通すため**にここに置いてあります
sys.path.insert(0, str(HERE / "vendor"))


def _quiet_logs(verbose: bool) -> None:
    """**同じ知らせを何十回も出さない。**

    マスタが読めないときの警告は、ファイルの数だけ出ます(1本につき3回)。
    知らせる値打ちがあるのは**1回目だけ**なので、口上は下の
    `_master_notice` が1行で出し、ここは黙らせます。
    """
    logging.getLogger("nippou").setLevel(
        logging.DEBUG if verbose else logging.CRITICAL)


def _master_notice() -> str:
    """停止内訳マスタが読めるか。**読めないなら、何が空になるかまで言う。**

    記号(`レ` `0` `G`)と分類(数字=管理ロス / カナ=突発 / 英字=ハンドリング)
    は、マスタが無くても出ます ── 分類は記号の字種で決まるので。
    **空になるのは「内訳」の1列だけ**(「休憩食事」などの名前)です。
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
        return f"停止内訳マスタ: {path}({len(codes)}記号)"
    return (f"停止内訳マスタが読めません({path})\n"
            "  → 停止内訳CSVの「内訳」欄が空になります"
            "(記号と分類は出ます)。--ref で場所を指定できます")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="エクセル(日付シート)を、日報管理ツールと同じCSVに変えます",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("inputs", nargs="+",
                        help="xlsx ファイル、またはそれが入っているフォルダ")
    parser.add_argument("--out", default="out",
                        help="CSVの出力先(既定: out)")
    parser.add_argument("--line", default="",
                        help="入れる先のライン(例: LS / HVC)。"
                             "省くとシートの呼び名から当てます")
    parser.add_argument("--ref", default="",
                        help="参照用マスタ(伝送用ファイル.sqlite3)のフォルダ。"
                             "停止の「内訳」名を出すのに使います")
    parser.add_argument("--tally", action="store_true",
                        help="機側データ集計も出します(VBAと同じ転記を1本のCSVに)")
    parser.add_argument("--check", action="store_true",
                        help="CSVを書かずに、読めるか・数が合うかだけ見ます")
    parser.add_argument("--quiet", action="store_true",
                        help="1本ごとの行を出しません")
    parser.add_argument("--verbose", action="store_true",
                        help="内部のログも出します(調べるとき)")
    args = parser.parse_args(argv)

    # **nippou を読み込む前に決めます** ── 置き場所は読み込み時に固まります
    if args.ref:
        os.environ["NIPPOU_GW_REF_DIR"] = str(Path(args.ref).expanduser())
    _quiet_logs(args.verbose)

    from converter import run

    books = run.gather(args.inputs)
    if not books:
        print("読む相手が見つかりません(.xlsx / .xlsm)", file=sys.stderr)
        return 1

    print(_master_notice())
    print(f"{len(books)} 本を読みます"
          + ("（確かめるだけ。CSVは書きません）" if args.check
             else f" → {Path(args.out).resolve()}"))

    def show(one, index, total):
        if args.quiet:
            return
        head = f"  [{index:>4}/{total}] {one.name[:34]:<34}"
        if not one.ok:
            print(f"{head} × {one.error}")
            return
        mark = "○" if one.verify.ok else "!"
        print(f"{head} {mark} {one.report_date} "
              f"{one.line_text}→{one.line} "
              f"{one.pages}ページ {one.rows}行  {one.verify.summary()}")

    found = run.convert(args.inputs, args.out, line=args.line,
                        dry_run=args.check, on_file=show)

    if args.tally:
        from converter import tally

        books = run.gather(args.inputs)
        table = tally.build(books)
        print()
        print(f"機側データ集計: {table.summary()}")
        for one in table.sheets:
            if not one.taken:
                print(f"   拾いませんでした {one.sheet or one.file}: {one.skipped}")
        for name, why in table.errors:
            print(f"   読めませんでした {name}: {why}")
        if table.rows and not args.check:
            print(f"   → {tally.write_csv(table, args.out)}")

    print()
    print(f"読めた        : {len(found.read_ok)}/{len(found.files)} 本")
    print(f"数が合った    : {len(found.verified)} 本")
    if found.suspect:
        # **黙って通しません。** 座標がずれていても数は出てしまうので、
        # ここで名指ししないと気づく機会がありません
        print(f"数が合わない  : {len(found.suspect)} 本"
              "  ← 座標がずれているおそれがあります")
        for one in found.suspect:
            print(f"   {one.name}: {one.verify.summary()}")
    bad = [f for f in found.files if not f.ok]
    if bad:
        print(f"読めなかった  : {len(bad)} 本")
        for one in bad:
            print(f"   {one.name}: {one.error}")
    if not args.check:
        print(f"書いたCSV     : {len(found.written)} 本")

    # 読めなかったものがあれば 1、数が合わないだけなら 2(書いてはある)
    if bad:
        return 1
    return 2 if found.suspect else 0


if __name__ == "__main__":
    raise SystemExit(main())
