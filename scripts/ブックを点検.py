#!/usr/bin/env python3
r"""ブックが**機側データ集計で読めるか**を、拾う前に見る

探しているのは「**停止3枠なのに読めないブック**」です。2枠のブックは今後
出てこない(3枠に統一される)ので、そちらは数えるだけにします。

    python scripts/ブックを点検.py <フォルダ or ブック…> [--all]

1シートにつき1行:

    読める?  枠    拾える行  ファイル / シート              理由
    ○        3枠        37   2026.08.23.xlsx / 2026年8月23日_機側
    ×        2枠         0   2024.05.11.xlsx / 2024年5月11日_機側  停止2枠のブックです…
    ×        ―          0   2025.03.02.xlsx / 2025年3月2日_HVC    シート名に「_機側」が…

最後に数をまとめます。**気にしてほしいのは「3枠なのに読めない」の数**です
(0ならそのまま集計できます)。既定では読めないものと3枠のものだけ並べます。
`--all` で全部出します。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

SUFFIXES = (".xlsx", ".xlsm")


def gather(inputs: list[str]) -> list[Path]:
    """渡されたフォルダ/ファイルから、見る相手を集める(名前順)。"""
    found: list[Path] = []
    for one in inputs:
        path = Path(one)
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*"))
                         if p.suffix.lower() in SUFFIXES
                         and not p.name.startswith("~$"))
        elif path.suffix.lower() in SUFFIXES:
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    import logging
    logging.getLogger("nippou").setLevel(logging.CRITICAL)
    from converter import tally

    show_all = "--all" in argv
    books = gather([a for a in argv if not a.startswith("--")])
    if not books:
        print(__doc__)
        return 2

    print(f"{len(books)} 冊を見ます\n")
    print("読める 枠    行数  ファイル / シート")
    print("─" * 78)

    counts = {"ok": 0, "empty": 0, "three_bad": 0, "two": 0, "other": 0}
    for book in books:
        for one in tally.check_book(book):
            frame = one["frame"] or "―"
            if one.get("empty"):
                counts["empty"] += 1           # 読めるが1行も拾えない
            elif one["ok"]:
                counts["ok"] += 1
            elif frame == tally.FRAME_TWO:
                counts["two"] += 1
            elif frame == tally.FRAME_THREE:
                counts["three_bad"] += 1       # **これが探しているもの**
            else:
                counts["other"] += 1
            if not (show_all or not one["ok"] or one.get("empty")
                    or frame == tally.FRAME_THREE):
                continue
            where = f"{one['file']}" + (f" / {one['sheet']}" if one["sheet"] else "")
            print(f"{'○' if one['ok'] else '×':<5} {frame:<5} "
                  f"{one['rows']:>5}  {where}")
            if one["why"]:
                print(f"{'':<17}└ {one['why']}")

    print("\n" + "─" * 78)
    print(f"読めるシート          : {counts['ok']}")
    print(f"読めるが0行           : {counts['empty']}")
    print(f"停止2枠(今後は無い)   : {counts['two']}")
    print(f"その他(対象外のシート): {counts['other']}")
    print(f"**停止3枠なのに読めない: {counts['three_bad']}**")
    if counts["three_bad"]:
        print("  → 上の × の行(枠=3枠)の理由を見てください")
    return 1 if counts["three_bad"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
