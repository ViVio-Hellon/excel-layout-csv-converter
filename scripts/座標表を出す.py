#!/usr/bin/env python3
"""座標表(`docs/座標表.md`)を、**写してあるコードから起こす**

手で書いた表は、コードが変わったときに黙って古くなります。読む相手が
`vendor/nippou/logic/nippou_sheet.py` なのだから、そこから出します。

    python scripts/座標表を出す.py            画面に出す
    python scripts/座標表を出す.py --write     docs/座標表.md を書き直す
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "vendor"))

from nippou import layout                        # noqa: E402
from nippou.logic import nippou_sheet as ns      # noqa: E402

HEAD = """# 日付シートの座標表

**このファイルは `scripts/座標表を出す.py` が書きます。** 手で直さないで
ください ── 読む相手は `vendor/nippou/logic/nippou_sheet.py` なので、
そちらを直してから出し直します。

1ファイル = **1日ぶん・3直ぜんぶ**の24時間割です(6行で1時間 × 24時間)。

## シートの頭

| セル | 中身 | 使い道 |
|---|---|---|
| E1 | 作業日 | **これが無いと読めません** |
| E2 | 梱包ライン(`機側` `HVC` …) | 当たりを付けるだけ。入れる先は選ぶ |
| E3 | 作業者(その日ぜんぶ) | 参考 |
| AA3 / AB3 | その日の合計(枚数 / 重量) | **検算** |
| T4 / V4 / X4 | 停止の合計(管理ロス / 突発 / ハンドリング) | **検算** |

## 直の帯

| 直 | 中身の行 | 合計の行 | 作業者 |
|---|---|---|---|
"""

TAIL = """
中身は {first}〜{last} 行、**1ページ {rows} 行**ずつに切ります(紙と同じ)。
合計の行は中身として読みません(枚数と重量しか入っていないため)。

## 列 → 日報の欄

**コイルと板で座標は変わりません。** 変わるのは「どの欄を使うか」だけ
です(下の「コイルと板」を参照)。

| 列 | 欄(DB) | 紙の見出し |
|---|---|---|
"""

NOTES = """
## 読まない列

**C列(時刻の目盛り)は読みません。** 6行で1時間の割り付けを示す印刷用の
飾りで、打った内容ではないためです。

## ロット№を下へ引き継ぎません

同じロットが時間帯をまたいで何行にも出てきます(10:30-11:15 のあと
11:15-11:30 …)。継続行にはロット№が書かれていませんが、**書いてある
とおり**に空欄のまま入れます。

引き継ぐと1ページ12行の中に同じLOTが4回並び、保存前のLOT重複チェックに
引っかかって、取り込んだページをあとから直せなくなります。枚数・重量・
作業時間は行ごとに入っているので、**合計は引き継がなくても合います**。

## コイルと板

実物(機側 = コイル / HVC = 板)を突き合わせた結果です。
**座標は同じで、使う欄が違うだけ**でした。

| 欄 | 列 | コイル | 板 |
|---|---|---|---|
| コイル縦割 | AQ | ○ | － |
| コイル横縦割 | AR | ○ | － |
| 係数処理ﾛｯﾄ数 | B | ○ | － |
| 合紙 | O | － | ○ |
| ＶＣ種別 | R | － | ○ |

どちらも同じ読み手で通り、**シート自身が持っている合計と完全に一致**
しました(枚数・重量・日計・停止)。

## 座標が合っているかの確かめ方

シートは自分で合計を持っています。読んだ明細を足した数と突き合わせれば、
**そのファイルで座標が合っていたか**が分かります:

```
python convert.py <フォルダ> --check
```

古いブックはテンプレートが年によって違うことがあり、列が1つずれていても
数は出てしまいます。**出たことと、合っていることは別**なので、
この確かめを通してください(`converter/check.py`)。
"""


def build() -> str:
    out = [HEAD]
    for shift, start, end, total in ns.SHIFT_BANDS:
        out.append(f"| {shift} | {start}〜{end} | {total} | "
                   f"{ns.WORKER_COLUMN}{start} |\n")
    out.append(TAIL.format(first=ns.FIRST_ROW, last=ns.LAST_ROW,
                           rows=ns.ROWS_PER_PAGE))
    labels = {c.field: (c.group, c.label) for c in layout.COLUMNS}
    for column, field in ns.COLUMNS.items():
        group, label = labels.get(field, ("", "(紙に無い)"))
        text = f"{group} {label}".strip() if group else label
        out.append(f"| {column} | `{field}` | {text} |\n")
    out.append(NOTES)
    return "".join(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="docs/座標表.md を書き直す")
    args = parser.parse_args()
    text = build()
    if args.write:
        target = HERE / "docs" / "座標表.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"書きました: {target}")
    else:
        print(text)
