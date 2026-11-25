/*
  chart.js — 折れ線・棒・複合・ドーナツを SVG で描く

  日報管理ツール(all-tools)の `app/static/js/chart.js` からの移植です。
  **追加ライブラリは使いません**(Chart.js も読み込みません)── 端末に
  入れられるものが増えないので、SVG を自分で組み立てます。

  **何を出すか・どう集計するかはサーバが決める**(`converter/plate_graph.py`)。
  ここは受け取った「ラベルと値の並び」を座標へ写す係です。色も、サーバが
  渡す印(`mark-*`)を class 名として書くだけで、何色かは `tokens.css` が
  決めます ── JSは「停止が何色か」を知りません。
*/

const CHART_W = 720, CHART_H = 220;
const PAD = { top: 16, right: 16, bottom: 34, left: 52 };
const DOT_R = 4;
const DONUT = { size: 200, thickness: 26 };
const SVG_NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}, text = "") {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) { node.setAttribute(k, String(v)); }
  if (text !== "") { node.textContent = text; }
  return node;
}

/** 値の並びから目盛りの上限を決める。**0のときも軸が潰れないように。** */
function ceiling(values) {
  const max = Math.max(0, ...values.filter((v) => Number.isFinite(v)));
  if (max <= 0) { return 1; }
  const digits = Math.pow(10, Math.floor(Math.log10(max)));
  return Math.ceil(max / digits) * digits;
}

function markOf(one) {
  return one && one.mark ? "mark-" + one.mark : "";
}

/** 横軸の字の大きさ(`components.css` の `.chart .label` と合わせます)。 */
const LABEL_FONT = 10;

/**
 * 日付は**月/日**だけにします(`2026年11月10日` → `11/10`)。
 *
 * 元の字は参照CSVの「日付」列そのままで、書き手によって
 * `2026年11月10日` だったり `2026-11-10` だったりします。年は横軸の
 * どこでも同じなので、**落としても迷いません**(期間は画面に出ています)。
 */
function shortLabel(text) {
  const raw = String(text ?? "").trim();
  let found = raw.match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/);
  if (found) { return `${Number(found[2])}/${Number(found[3])}`; }
  found = raw.match(/^(\d{1,2})[-/.月](\d{1,2})日?$/);
  if (found) { return `${Number(found[1])}/${Number(found[2])}`; }
  return raw;
}

/** だいたいの字幅(全角は1文字ぶん、半角は半分強)。**測るために描きません。** */
function labelWidth(text) {
  let units = 0;
  for (const ch of String(text)) {
    units += /[\u0020-\u007e\uff61-\uff9f]/.test(ch) ? 0.56 : 1;
  }
  return units * LABEL_FONT;
}

/** 斜めにする角度(度)。**見た目と、要る高さの両方がこれで決まります。** */
const SLANT = 40;

/**
 * 横軸をどう出すか**先に決めます**(描く前に)。
 *
 * ひと月ぶん出すと `2026年11月10日` が12個並び、重なって読めません。
 * 順に逃がします。
 *
 *     1. 月/日 だけにする
 *     2. 入らなければ**斜め**にする ── 斜めなら必要な間隔は字の高さ
 *        ぶんで済むので、間引かずに全部の日を出せます
 *     3. 斜めでも入らないほど日が多ければ、そこで初めて**間引く**
 *
 * 間引きは最後です ── 日が飛んだ軸は「その日のデータが無い」と
 * 読み違えられます。
 *
 * **`extra` が要ります。** 斜めにした字は軸の下へ left-down に伸びるので、
 * そのぶん器(viewBox)を縦に伸ばさないと**下が切れます**(切れました)。
 * ここで要る高さを返し、呼ぶ側が器を伸ばします。
 */
function labelPlan(labels, plotW) {
  const shown = (labels || []).map(shortLabel);
  const step = shown.length ? plotW / shown.length : plotW;
  const widest = Math.max(1, ...shown.map(labelWidth));
  const flat = Math.ceil((widest + 6) / step);      // まっすぐ並べるなら何個おきか
  const slant = Math.ceil((LABEL_FONT + 3) / step); // 斜めなら何個おきか
  const rotate = flat > 1 && slant < flat;
  const every = Math.max(1, rotate ? slant : flat);
  // 斜めの字が軸の下に要する高さ(+ 下の余白)
  const drop = rotate
    ? widest * Math.sin((SLANT * Math.PI) / 180) + LABEL_FONT * 0.4
    : LABEL_FONT;
  const need = 12 + Math.ceil(drop) + 4;            // 軸から下に要るぶん
  return { shown, every, rotate, extra: Math.max(0, need - PAD.bottom) };
}

/** 横軸のラベルを置く(**どう出すかは `labelPlan` が決めています**)。 */
function paintLabels(svg, plan, step, axisY) {
  const y = axisY + 12;
  plan.shown.forEach((text, i) => {
    if (i % plan.every !== 0) { return; }
    const x = PAD.left + step * (i + 0.5);
    const attrs = { class: "label", x, y,
                    "text-anchor": plan.rotate ? "end" : "middle" };
    if (plan.rotate) { attrs.transform = `rotate(-${SLANT} ${x} ${y})`; }
    svg.appendChild(el("text", attrs, text));
  });
}

/** 1枚のグラフ(棒でも折れ線でも同じ骨格)。 */
function chart(series, labels) {
  const W = CHART_W;
  const plotW = W - PAD.left - PAD.right;
  // **先に横軸の出し方を決めます** ── 斜めにするなら器を縦に伸ばします
  const plan = labelPlan(labels, plotW);
  const H = CHART_H + plan.extra;
  const svg = el("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `${series.title}(${series.unit})`,
  });
  const values = series.values || [];
  const top = ceiling(values);
  // 絵の高さは伸ばしません(伸ばしたぶんは**軸の下**の字のため)
  const plotH = CHART_H - PAD.top - PAD.bottom;
  const y = (v) => PAD.top + plotH - (Math.max(0, v) / top) * plotH;

  for (let i = 0; i <= 4; i += 1) {
    const value = (top / 4) * i;
    const yy = y(value);
    svg.appendChild(el("line", { class: "gridline", x1: PAD.left,
                                 x2: W - PAD.right, y1: yy, y2: yy }));
    svg.appendChild(el("text", { class: "label", x: PAD.left - 6, y: yy + 3,
                                 "text-anchor": "end" },
                       String(Math.round(value * 100) / 100)));
  }
  svg.appendChild(el("line", { class: "axis", x1: PAD.left, x2: PAD.left,
                               y1: PAD.top, y2: PAD.top + plotH }));

  if (!values.length) {
    svg.appendChild(el("text", { class: "label", x: W / 2, y: H / 2,
                                 "text-anchor": "middle" },
                       "この期間のデータはありません"));
    return svg;
  }

  const step = plotW / values.length;
  if (series.kind === "bar") {
    const width = Math.min(48, step * 0.6);
    const marks = series.pointClasses || [];
    values.forEach((v, i) => {
      const cx = PAD.left + step * (i + 0.5);
      const yy = y(v);
      const mark = marks[i] ? " mark-" + marks[i] : "";
      svg.appendChild(el("rect", { class: "bar" + mark, x: cx - width / 2,
                                   y: yy, width,
                                   height: PAD.top + plotH - yy }));
    });
  } else {
    const points = values.map((v, i) =>
      `${PAD.left + step * (i + 0.5)},${y(v)}`).join(" ");
    svg.appendChild(el("polyline", { class: "line", points }));
    values.forEach((v, i) => {
      svg.appendChild(el("circle", { class: "dot", r: DOT_R,
                                     cx: PAD.left + step * (i + 0.5), cy: y(v) }));
    });
  }
  paintLabels(svg, plan, step, PAD.top + plotH);
  return svg;
}

/*
  複合グラフ ── 棒(その日の枚数)+ 折れ線(累積枚数)、またはライン別の線

  **目盛りを左右で分ける。** その日の枚数は数百、累積は数万になるので、
  同じ目盛りに乗せると棒が床に貼り付いて高さの差が読めません。
  どちらの軸かは**サーバが決めます**(`series[].axis`)。
*/
function combo(tile) {
  const labels = tile.labels || [];
  const series = tile.series || [];
  const W = CHART_W;
  // 器の高さは**横軸の出し方が決まってから**入れます(下の `setAttribute`)
  const svg = el("svg", {
    class: "chart", viewBox: `0 0 ${W} ${CHART_H}`, role: "img",
    "aria-label": `${tile.title}(${tile.unit})`,
  });
  const pick = (axis) => series.filter((s) => (s.axis || "left") === axis);
  const topOf = (axis) => ceiling(pick(axis).flatMap((s) => s.values || []));
  const left = topOf("left");
  const right = topOf("right");
  const rightPad = pick("right").length
    ? Math.max(PAD.right, 12 + String(Math.round(right)).length * 7)
    : PAD.right;
  const plotW = W - PAD.left - rightPad;
  const plotH = CHART_H - PAD.top - PAD.bottom;
  const plan = labelPlan(labels, plotW);
  const H = CHART_H + plan.extra;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const y = (v, axis) => PAD.top + plotH
    - (Math.max(0, v) / ((axis === "right") ? right : left)) * plotH;

  for (let i = 0; i <= 4; i += 1) {
    const yy = PAD.top + plotH - (plotH / 4) * i;
    svg.appendChild(el("line", { class: "gridline", x1: PAD.left,
                                 x2: W - rightPad, y1: yy, y2: yy }));
    svg.appendChild(el("text", { class: "label", x: PAD.left - 6, y: yy + 3,
                                 "text-anchor": "end" },
                       String(Math.round((left / 4) * i * 100) / 100)));
    if (pick("right").length) {
      svg.appendChild(el("text", { class: "label", x: W - rightPad + 6,
                                   y: yy + 3, "text-anchor": "start" },
                         String(Math.round((right / 4) * i))));
    }
  }
  svg.appendChild(el("line", { class: "axis", x1: PAD.left, x2: PAD.left,
                               y1: PAD.top, y2: PAD.top + plotH }));

  if (!labels.length) {
    svg.appendChild(el("text", { class: "label", x: W / 2, y: H / 2,
                                 "text-anchor": "middle" },
                       tile.empty || "この期間のデータはありません"));
    return svg;
  }

  const step = plotW / labels.length;
  series.forEach((one) => {
    const axis = one.axis || "left";
    const values = one.values || [];
    if (one.kind === "bar") {
      const width = Math.min(48, step * 0.6);
      values.forEach((v, i) => {
        const cx = PAD.left + step * (i + 0.5);
        const yy = y(Math.max(0, v || 0), axis);
        const height = y(0, axis) - yy;
        if (height <= 0) { return; }   // 0の日に線だけ残さない
        svg.appendChild(el("rect", { class: "bar " + markOf(one),
                                     x: cx - width / 2, y: yy, width, height }));
      });
      return;
    }
    const points = values.map((v, i) =>
      `${PAD.left + step * (i + 0.5)},${y(v, axis)}`).join(" ");
    // **線を2回描く。** 1本目は地の色で太く(縁取り)、2本目が本体 ──
    // 縁取りが無いと、線が棒を横切るところで棒に溶けて消えます
    svg.appendChild(el("polyline", { class: "line line--halo", points }));
    svg.appendChild(el("polyline", { class: "line " + markOf(one), points }));
    values.forEach((v, i) => {
      svg.appendChild(el("circle", { class: "dot " + markOf(one), r: DOT_R,
                                     cx: PAD.left + step * (i + 0.5),
                                     cy: y(v, axis) }));
    });
  });
  paintLabels(svg, plan, step, PAD.top + plotH);
  return svg;
}

/**
 * 複合グラフの凡例。**見本の形を絵の中の形に合わせる** ── 棒は四角、
 * 線は横棒。同じ色の2つが並んだとき、これが手がかりになります。
 */
function comboLegend(tile) {
  const list = document.createElement("ul");
  list.className = "dlg";
  (tile.series || []).forEach((one) => {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "dlg__swatch"
      + (one.kind === "bar" ? "" : " dlg__swatch--line") + " " + markOf(one);
    const text = document.createElement("span");
    text.className = "dlg__text";
    const side = (one.axis || "left") === "right" ? "右目盛り" : "左目盛り";
    text.textContent = `${one.title}(${one.unit || tile.unit}・${side})`;
    item.append(swatch, text);
    list.append(item);
  });
  return list;
}

/* ドーナツ ── 「で、何分なの」を探させないよう真ん中に合計を置く */
function polar(cx, cy, r, ratio) {
  const angle = (ratio * 360 - 90) * (Math.PI / 180);
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

function donut(tile) {
  const size = DONUT.size;
  const cx = size / 2, cy = size / 2;
  const outer = size / 2 - 2;
  const inner = outer - DONUT.thickness;
  const svg = el("svg", {
    class: "donut", viewBox: `0 0 ${size} ${size}`, role: "img",
    "aria-label": `${tile.title}(${tile.unit})`,
  });
  const slices = tile.slices || [];
  const total = slices.reduce((sum, s) => sum + (s.value || 0), 0);
  if (!total) {
    svg.appendChild(el("circle", { class: "donut__empty", cx, cy,
                                   r: (outer + inner) / 2,
                                   "stroke-width": DONUT.thickness }));
    return svg;
  }
  let from = 0;
  slices.forEach((slice) => {
    const ratio = (slice.value || 0) / total;
    const to = from + ratio;
    if (ratio >= 0.999) {
      // まるごと1色のときは弧では描けない(始点と終点が同じ)ので輪にする
      svg.appendChild(el("circle", {
        class: "donut__ring " + markOf(slice), cx, cy,
        r: (outer + inner) / 2, "stroke-width": DONUT.thickness }));
    } else {
      const [x1, y1] = polar(cx, cy, outer, from);
      const [x2, y2] = polar(cx, cy, outer, to);
      const [x3, y3] = polar(cx, cy, inner, to);
      const [x4, y4] = polar(cx, cy, inner, from);
      const large = ratio > 0.5 ? 1 : 0;
      svg.appendChild(el("path", {
        class: "donut__slice " + markOf(slice),
        d: `M${x1},${y1} A${outer},${outer} 0 ${large} 1 ${x2},${y2}`
           + ` L${x3},${y3} A${inner},${inner} 0 ${large} 0 ${x4},${y4} Z`,
      }));
    }
    from = to;
  });
  svg.appendChild(el("text", { class: "donut__total", x: cx, y: cy + 2,
                               "text-anchor": "middle" }, tile.total || ""));
  svg.appendChild(el("text", { class: "donut__unit", x: cx, y: cy + 20,
                               "text-anchor": "middle" }, tile.unit || ""));
  return svg;
}

/** ドーナツの凡例。**色だけでは何の色か分からない。** */
function legend(tile) {
  const list = document.createElement("ul");
  list.className = "dlg";
  (tile.slices || []).forEach((slice) => {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "dlg__swatch " + markOf(slice);
    const text = document.createElement("span");
    text.className = "dlg__text";
    text.textContent = `${slice.label} ${slice.value}${tile.unit}`;
    const pct = document.createElement("b");
    pct.textContent = `${slice.pct}%`;
    item.append(swatch, text, pct);
    list.append(item);
  });
  return list;
}

/** 表のタイル。**サーバが決めた列と行をそのまま**。 */
function table(tile) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const node = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  (tile.columns || []).forEach((name, i) => {
    const cell = document.createElement("th");
    cell.textContent = name;
    if (i > 0) { cell.className = "num"; }
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  (tile.rows || []).forEach((row) => {
    const line = document.createElement("tr");
    row.forEach((value, i) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (i > 0) { cell.className = "num"; }
      line.append(cell);
    });
    body.append(line);
  });
  node.append(head, body);
  wrap.append(node);
  return wrap;
}

/** タイル1枚の中身を描く(器は呼び手が用意する)。 */
export function paintTile(host, tile) {
  if (!host || !tile) { return; }
  host.replaceChildren();
  if (!tile.has_data) {
    const note = document.createElement("p");
    note.className = "gtile__empty";
    note.textContent = tile.empty || "データがありません";
    host.append(note);
    return;
  }
  if (tile.kind === "number") {
    const value = document.createElement("b");
    value.className = "gtile__value";
    value.textContent = tile.value;
    const unit = document.createElement("span");
    unit.className = "gtile__unit";
    unit.textContent = tile.unit;
    host.append(value, unit);
    return;
  }
  if (tile.kind === "table") { host.append(table(tile)); return; }
  if (tile.kind === "donut") { host.append(donut(tile), legend(tile)); return; }
  if (tile.kind === "combo") {
    host.append(combo(tile), comboLegend(tile));
    return;
  }
  if (tile.kind === "bar" || tile.kind === "line") {
    host.append(chart({ title: tile.title, unit: tile.unit, kind: tile.kind,
                        values: tile.values,
                        pointClasses: tile.point_classes || [] },
                      tile.labels || []));
  }
}

export { chart, combo, donut };
