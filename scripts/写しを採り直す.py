#!/usr/bin/env python3
r"""`vendor/` を移行元から採り直す

VENDOR.md の手順をそのまま道具にしたものです。**手で選びません** ──
実物のブックで変換を1回走らせ、そのとき読み込まれたモジュール
(`sys.modules` の `nippou` / `dbkit`)を写します。手で選ぶと、遅延
import(`guess_line` の中など)を取りこぼします。

    python scripts/写しを採り直す.py <移行元のフォルダ> <実物のブック…>

**書く前に、いまの写しと見比べて数を出します。** 増えた・消えた・
中身が変わったファイルが分かるので、「何が入ったのか」を言えます。
ブックを渡さないときは `tests/_fixture.py` の組み立てブックで走らせます
(通る道が少し狭くなるので、実物があるときは渡してください)。
"""
from __future__ import annotations

import filecmp
import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"


def run_once(source: Path, books: list[str]) -> list[str]:
    """移行元を先に通して**使う道を全部**走らせ、読み込まれたモジュール名を返す。

    走らせるのは3つです ── 1つでは足りません:

    1. ラインを指定せずに変換(`guess_line` が動く。その中の遅延 import
       `logic/line_target` と `services/targets` はここでしか読まれません)
    2. ラインを指定して変換(指定が優先される道)
    3. 機側データ集計(`layout` / `csv_export` / `formula` を遅延 import)

    **1を飛ばすと、写しから2ファイル消えます。** 最初に採ったときに
    実際に起きた取りこぼしで、VENDOR.md に書いてあるのがこれです。
    """
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(source))               # **写しより先**
    logging.getLogger("nippou").setLevel(logging.CRITICAL)

    import nippou
    if not str(Path(nippou.__file__).resolve()).startswith(str(source)):
        raise SystemExit(f"移行元が通っていません: {nippou.__file__}")

    from converter import run

    with tempfile.TemporaryDirectory(prefix="revendor-") as work:
        if not books:
            sys.path.insert(0, str(ROOT))
            from tests._fixture import day_sheet, write_xlsx
            made = Path(work) / "a.xlsx"
            write_xlsx(made, day_sheet())
            books = [str(made)]
        # ①ラインを当てさせる(guess_line の中の遅延 import を通す)
        found = run.convert(books, Path(work) / "out1")
        ok = [f for f in found.files if f.ok]
        print(f"変換(ライン自動): {len(ok)}/{len(found.files)} 本 読めました / "
              f"CSV {len(found.written)} 本")
        for one in found.files:
            if not one.ok:
                print(f"  × {one.name}: {one.error}")
        # ②ラインを指定する道
        run.convert(books, Path(work) / "out2", line="LS")
        # ③機側データ集計(変換が出したCSVを材料にする道も通す)
        from converter import tally
        made = sorted(Path(work).joinpath("out1").rglob("集計明細_*.csv"))
        stops = sorted(Path(work).joinpath("out1").rglob("停止内訳_*.csv"))
        table = tally.build(books + [str(p) for p in made + stops])
        print(f"機側データ集計: {table.summary()[:60]}")
        if table.rows:
            tally.write_csv(table, Path(work) / "tally")
    return sorted(name for name in sys.modules
                  if name.split(".")[0] in ("nippou", "dbkit"))


def files_of(names: list[str], source: Path) -> list[Path]:
    """モジュール名 → 移行元の中の道(相対)。"""
    found: list[Path] = []
    for name in names:
        module = sys.modules.get(name)
        path = getattr(module, "__file__", None)
        if not path:
            continue
        here = Path(path).resolve()
        try:
            found.append(here.relative_to(source))
        except ValueError:
            continue                              # 写しのほうを読んだもの
    return sorted(set(found))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = Path(sys.argv[1]).resolve()
    if not (source / "nippou").is_dir():
        print(f"移行元が見つかりません: {source}", file=sys.stderr)
        return 2

    names = run_once(source, sys.argv[2:])
    wanted = files_of(names, source)
    print(f"読み込まれたモジュール: {len(names)} / 写すファイル: {len(wanted)}")

    before = sorted(p.relative_to(VENDOR) for p in VENDOR.rglob("*.py"))
    added = [p for p in wanted if p not in before]
    gone = [p for p in before if p not in wanted]
    changed = [p for p in wanted
               if p in before and not filecmp.cmp(source / p, VENDOR / p,
                                                  shallow=False)]
    print(f"増える: {len(added)} / 消える: {len(gone)} / 中身が変わる: {len(changed)}")
    for label, items in (("増える", added), ("消える", gone),
                         ("変わる", changed)):
        for path in items:
            print(f"  {label}: {path}")

    shutil.rmtree(VENDOR, ignore_errors=True)
    for path in wanted:
        target = VENDOR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / path, target)
    print(f"→ {VENDOR} に {len(wanted)} ファイル写しました")
    print("**ここで終わりではありません。** tests を走らせ、"
          "実物での突き合わせをやり直してください(VENDOR.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
