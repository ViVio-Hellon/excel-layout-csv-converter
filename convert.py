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
import tempfile
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


#: `--stop-csv` のときだけ使う、**この実行かぎりの**設定置き場。
#: 変数で持っているのは、抜けるまで消えないようにするため
_RUN_DIR: "tempfile.TemporaryDirectory | None" = None


def _use_stop_csv(text: str) -> str:
    """`--stop-csv` の置き場所を `nippou` 側へ渡す。**この実行かぎり。**

    受け方は画面と同じです(フォルダでも `停止内訳.csv` そのものでも ──
    `user_settings.stop_csv_path`)。「画面では効くのにコマンドでは効かない」
    を作らないため。

    渡し先は向こうの**端末設定**(`user_settings` の `stop_reason_file`)
    しかありません。同じ端末に日報管理ツールが入っていると、そちらの
    設定ファイルに書き込むことになるので、**使い捨ての置き場所を作って
    そこへ書きます**(`NIPPOU_APP_DIR`)── 人の設定を黙って書き換えない。
    """
    global _RUN_DIR
    from converter import user_settings

    path = user_settings.stop_csv_path(user_settings.Settings(stop_csv=text))
    if not path:
        return f"停止内訳CSVの書き方が読めません: {text}"
    _RUN_DIR = tempfile.TemporaryDirectory(prefix="convert-conf-")
    os.environ["NIPPOU_APP_DIR"] = _RUN_DIR.name
    from nippou import user_settings as theirs
    theirs.save("stop_reason_file", str(path))
    return f"停止内訳CSV: {path}"


def _master_notice() -> str:
    """停止内訳が読めるか。**読めないなら、何が空になるかまで言う。**

    言葉は画面と同じものを使います(`converter/stop_reasons.py`)── 同じ
    ことを2通りに言うと、どちらが本当か分からなくなります。
    """
    from converter import stop_reasons
    return stop_reasons.notice()[1]


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
    parser.add_argument("--stop-csv", default="", dest="stop_csv",
                        help="停止内訳CSVの置き場所(フォルダでも、"
                             "停止内訳.csv そのものでも)。**マスタより先に"
                             "読みます**(分類ごと)")
    parser.add_argument("--tally", action="store_true",
                        help="機側データ集計も出します(VBAと同じ転記を1本のCSVに)")
    parser.add_argument("--any-sheet", action="store_true", dest="any_sheet",
                        help="集計で、シート名の「_機側」を問わない。"
                             "**VBAは機側のシートだけ見ます**(既定は同じ)")
    parser.add_argument("--check", action="store_true",
                        help="CSVを書かずに、読めるか・数が合うかだけ見ます")
    parser.add_argument("--quiet", action="store_true",
                        help="1本ごとの行を出しません")
    parser.add_argument("--verbose", action="store_true",
                        help="内部のログも出します(調べるとき)")
    args = parser.parse_args(argv)

    # **設定の「ログの置き場所」は、コマンドからでも効かせます** ──
    # 画面では共有へ出るのにコマンドでは端末の中、という食い違いを作らない
    # ため(書けなければ端末の中へ戻し、その理由も残します)
    from converter import logging_utils, user_settings

    refused = logging_utils.use_folder(user_settings.load().log_dir)
    if refused:
        print(refused, file=sys.stderr)

    # **nippou を読み込む前に決めます** ── 置き場所は読み込み時に固まります
    if args.ref:
        os.environ["NIPPOU_GW_REF_DIR"] = str(Path(args.ref).expanduser())
    stop_csv_note = _use_stop_csv(args.stop_csv) if args.stop_csv else ""
    _quiet_logs(args.verbose)

    from converter import run

    books = run.gather(args.inputs)
    if not books:
        print("読む相手が見つかりません(.xlsx / .xlsm)", file=sys.stderr)
        return 1

    if stop_csv_note:
        print(stop_csv_note)
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
        if one.note:
            # **黙って直しません。** 読むときに手を入れたことは必ず出す
            print(f"{' ' * len(head)}   ※ {one.note}")

    found = run.convert(args.inputs, args.out, line=args.line,
                        dry_run=args.check, on_file=show)

    if args.tally:
        from converter import tally

        books = run.gather(args.inputs)
        table = tally.build(books, any_sheet=args.any_sheet)
        print()
        print(f"機側データ集計: {table.summary()}")
        for one in table.sheets:
            if not one.taken:
                print(f"   拾いませんでした {one.sheet or one.file}: {one.skipped}")
        for name, why in table.errors:
            print(f"   読めませんでした {name}: {why}")
        if table.rows and not args.check:
            # **ラインごとに1本**出ます(機側には板に無い欄があるので)
            for made in tally.write_csvs(table, args.out):
                print(f"   → {made}")

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
