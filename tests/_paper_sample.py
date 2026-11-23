r"""印刷用HTML(紙)の**本物の形**

`ViVio-Hellon/all-tools` の `nippou/reporting/print_format.build_print_html`
が出したものから、`<style>` だけ省いたものです。読み手
(`converter/plate_paper.py`)が**向こうの本物の形**を読めることを
確かめるために置いています ── 自分で組み立てた形を自分で読んでも、
向こうが変わったことに気づけません。

作り直すとき(向こうの紙の形が変わったとき):

    python -c "import sys; sys.path.insert(0, '<all-tools>/tools/nippou'); ..."

で出したものを、`<style>` を省いてここへ貼ります。
"""
SAMPLE = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>日報 2026年11月17日 L-1 1直 第1ページ</title>
<style>/* 省いています */</style>
</head>
<body>
<div class="toolbar">
  <button type="button" onclick="window.print()">印刷する</button>
  <span class="hint">A4 横1枚で刷れます。刷り終わったら、この窓は閉じてかまいません</span>
</div>
<section class="sheet">
<div class="head">
<table class="head__keys"><tr><th>作業日・直</th><td>2026年11月17日1直</td></tr><tr><th>梱包ライン</th><td>L-1</td></tr><tr><th>作業者名</th><td>除外 太郎</td></tr></table>
<div class="head__title"><h1>梱　包　実　績　日　報　表</h1></div>
<div class="head__right">
<div class="head__box"><b>ページ</b>1</div>
<div class="head__box"><b>昼稼働</b>有</div>
<div class="head__box head__box--reason"><b>その他</b></div>
</div>
</div>
<div class="backfill-stamp">自動</div>
<div class="note">※作業停止時間は5分以上で時間を記入</div>
<table>
<thead>
<tr><th rowspan="2">行</th><th rowspan="2">ロット№</th><th rowspan="2">材・調質</th><th colspan="1">製品寸法</th><th rowspan="2">検入枚数</th><th colspan="4">梱包作業時間</th><th rowspan="2">作業人数</th><th rowspan="2">合紙</th><th colspan="2">梱包数量</th><th colspan="1">ＶＣ種別</th><th colspan="1">etc</th><th colspan="2">作業停止①</th><th colspan="2">作業停止②</th><th colspan="2">作業停止③</th><th colspan="2">実績合計</th></tr>
<tr><th>厚×幅×丈</th><th>開始 時</th><th>開始 分</th><th>終了 時</th><th>終了 分</th><th>個装単位 枚数</th><th>梱包単位 包数</th><th>両面・片面</th><th>反転・EX etc</th><th>記号</th><th>時間(分)</th><th>記号</th><th>時間(分)</th><th>記号</th><th>時間(分)</th><th>枚数</th><th>重量</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>X7101</td><td>SS400</td><td>3.2×1219×2438</td><td>40</td><td>08</td><td>15</td><td>09</td><td>40</td><td>2</td><td>有</td><td>40</td><td>2</td><td>両面</td><td></td><td>0</td><td>60</td><td></td><td></td><td></td><td></td><td>40</td><td>2.40</td></tr><tr><td>2</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>3</td><td>X7103</td><td>SS400</td><td>3.2×1219×2438</td><td>40</td><td>08</td><td>15</td><td>09</td><td>40</td><td>2</td><td>有</td><td>40</td><td>2</td><td>両面</td><td></td><td>0</td><td>60</td><td></td><td></td><td></td><td></td><td>40</td><td>2.40</td></tr><tr><td>4</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>5</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>6</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>7</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>8</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>9</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>10</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>11</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr><tr><td>12</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</tbody>
</table>
<div class="totals">
<table>
<thead>
<tr><th>負荷計算後</th><th colspan="3">直実績合計</th></tr>
<tr><th>係数Lot数</th><th>ﾛｯﾄ数</th><th>枚数</th><th>重量</th></tr>
</thead>
<tbody>
<tr>
<td></td>
<td></td>
<td></td>
<td><span class="kg"> Kg</span></td>
</tr>
</tbody>
</table>
</div>

<div class="generated-at">生成日時: 2026-11-17 18:00 / 日報 2026年11月17日 L-1 1直 第1ページ</div>
</section>
</body>
</html>
'''
