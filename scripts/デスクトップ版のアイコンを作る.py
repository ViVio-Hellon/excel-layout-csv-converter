#!/usr/bin/env python3
"""デスクトップ版のアイコンを作る(`src-tauri/icons/`)

**追加ライブラリは使いません**(`zlib` と `struct` だけで PNG と ICO を
組み立てます)。絵を描く道具を入れないのは、このリポジトリの約束です。

    python scripts/デスクトップ版のアイコンを作る.py

出すもの ── Tauri が `tauri.conf.json` の `bundle.icon` で要るもの:

    icons/32x32.png  icons/128x128.png  icons/icon.png(256)  icons/icon.ico

絵柄は「表(エクセル)から CSV の行が出る」ことだけ:
紺の角丸に、白い表のマスと、右下へ流れる3本の線。
**文字は入れません**(小さいと読めず、かえって何のアイコンか分からない)。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "src-tauri" / "icons"

#: 色(`app/static/css/tokens.css` の紺と白に合わせる)
INK = (29, 91, 143)          # 紺
PAPER = (255, 255, 255)      # 白
LINE = (31, 109, 74)         # CSVの行(緑)


def blank(size: int) -> list[list[tuple[int, int, int, int]]]:
    return [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]


def rounded(px, size: int, color, radius_ratio: float = 0.18) -> None:
    """角丸の四角で塗る(背景)。"""
    radius = size * radius_ratio
    for y in range(size):
        for x in range(size):
            dx = min(x + 0.5, size - x - 0.5)
            dy = min(y + 0.5, size - y - 0.5)
            if dx >= radius or dy >= radius:
                px[y][x] = (*color, 255)
                continue
            if (radius - dx) ** 2 + (radius - dy) ** 2 <= radius * radius:
                px[y][x] = (*color, 255)


def box(px, size: int, x0: float, y0: float, x1: float, y1: float, color) -> None:
    for y in range(max(0, int(y0)), min(size, int(y1) + 1)):
        for x in range(max(0, int(x0)), min(size, int(x1) + 1)):
            px[y][x] = (*color, 255)


def draw(size: int):
    """1枚ぶん組み立てる。"""
    px = blank(size)
    rounded(px, size, INK)

    # 表(左上)── 2×2のマス
    pad = size * 0.18
    cell = size * 0.17
    gap = max(1.0, size * 0.035)
    for row in range(2):
        for col in range(2):
            x0 = pad + col * (cell + gap)
            y0 = pad + row * (cell + gap)
            box(px, size, x0, y0, x0 + cell, y0 + cell, PAPER)

    # CSVの行(右下へ3本)
    thick = max(1.0, size * 0.055)
    for i in range(3):
        y0 = size * 0.60 + i * (thick + max(1.0, size * 0.045))
        x0 = size * 0.30 + i * size * 0.08
        box(px, size, x0, y0, size * 0.84, y0 + thick, LINE)
    return px


def png(px) -> bytes:
    """PNG(RGBA)1枚。"""
    size = len(px)
    raw = bytearray()
    for row in px:
        raw.append(0)                             # フィルタ無し
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    head = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def ico(sizes: list[int]) -> bytes:
    """ICO(中身は PNG)。Windows はこれを窓とタスクバーに使います。"""
    images = [png(draw(size)) for size in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def main() -> int:
    ICONS.mkdir(parents=True, exist_ok=True)
    made = []
    for name, size in (("32x32.png", 32), ("128x128.png", 128),
                       ("icon.png", 256)):
        path = ICONS / name
        path.write_bytes(png(draw(size)))
        made.append(path)
    path = ICONS / "icon.ico"
    path.write_bytes(ico([16, 32, 48, 256]))
    made.append(path)
    for path in made:
        print(f"{path.relative_to(ICONS.parent.parent)} "
              f"({path.stat().st_size:,} バイト)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
