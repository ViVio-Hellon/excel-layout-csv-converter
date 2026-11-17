/*
  views/plate.js — 板集計タブ

  参照パス配下のラインを一覧に出し、**選んだラインと期間**で日ごとの
  CSVを1つにまとめ、グラフを出し、CSV か Excel で書き出します。

  **何を出すか・どう集計するかはサーバが決める**(`converter/plate_graph.py`)。
  ここは絵を器へ入れる係です ── 数も文言もサーバが作ったものを出します。

  【この画面へ2度目に来たとき】
  ES モジュールは一度しか読まれないので、**要素は毎回引き直します**。
  配線は `wire()` に閉じ、タブに入ったときに一覧を読み直します
  (配布設定や設定タブで参照パスを変えた直後でも、入り直せば追います)。
*/
import { api, reason } from "../api.js";
import { paintTile } from "../chart.js";
import * as toast from "../toast.js";

const $ = (id) => document.getElementById(id);

/** いま出ているぶん(書き出しのときに同じ指定を使う) */
let known = { lines: [], loaded: false };

function chosen() {
  return Array.from(document.querySelectorAll("#plate-lines input:checked"))
    .map((box) => box.value);
}

function span() {
  return { start: $("plate-start").value, end: $("plate-end").value };
}

function ask() {
  return Object.assign({ lines: chosen() }, span());
}

/** ラインの一覧。**集計が無いラインも出して、無いと言います。** */
function paintLines(data) {
  const host = $("plate-lines");
  host.replaceChildren();
  (data.lines || []).forEach((one, index) => {
    const label = document.createElement("label");
    label.className = "line-pick" + (one.has_aggregate ? "" : " off");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = one.name;
    box.disabled = !one.has_aggregate;
    // **1つ目は選んでおきます** ── 開いてすぐ「グラフを出す」が押せます
    box.checked = one.has_aggregate && index === 0;
    const name = document.createElement("b");
    name.textContent = one.name;
    const note = document.createElement("span");
    note.textContent = one.has_aggregate
      ? `${one.days} 日ぶん(${one.first} 〜 ${one.last})`
      : "集計フォルダがありません";
    label.append(box, name, note);
    host.append(label);
  });
  if (!(data.lines || []).length) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "参照パスの下にラインのフォルダが見つかりません。";
    host.append(note);
  }
}

export function load() {
  return api("/api/plate").then((result) => {
    const data = result.data || {};
    const problem = $("plate-problem");
    problem.hidden = !!data.ok;
    problem.textContent = data.problem || "";
    $("plate-run").disabled = !data.ok;
    $("plate-base").textContent = data.base || "";
    if (!data.ok) { $("plate-lines").replaceChildren(); return; }
    known = { lines: data.lines || [], loaded: true };
    paintLines(data);
    if (data.span && !$("plate-start").value) {
      $("plate-start").value = data.span.start;
      $("plate-end").value = data.span.end;
    }
    $("plate-max").textContent = data.max_days;
  });
}

/** 読めなかったものを出す。**黙って捨てません。** */
function paintSkipped(summary) {
  const host = $("plate-skipped");
  const skipped = (summary && summary.skipped) || [];
  if (!skipped.length) { host.hidden = true; return; }
  host.hidden = false;
  host.textContent = `読めなかったもの ${skipped.length} 件:\n`
    + skipped.slice(0, 12).map((one) =>
      `・${one.line || ""} ${one.date || ""} ${one.reason || ""}`).join("\n")
    + (skipped.length > 12 ? `\n・ほか ${skipped.length - 12} 件` : "");
}

/** タイルの器を1枚作る(**並ぶタイルは期間とラインで入れ替わります**)。 */
function shellFor(tile) {
  const box = document.createElement("div");
  box.className = "gtile"
    + (["bar", "line", "donut"].includes(tile.kind) ? " gtile--wide" : "")
    + (["combo", "table"].includes(tile.kind) ? " gtile--full" : "");
  if (tile.tone) { box.dataset.tone = tile.tone; }
  const head = document.createElement("div");
  head.className = "gtile__head";
  const title = document.createElement("h3");
  title.textContent = tile.title;
  const note = document.createElement("span");
  note.className = "gtile__note";
  note.textContent = tile.note || tile.unit || "";
  head.append(title, note);
  const body = document.createElement("div");
  body.className = "gtile__body";
  box.append(head, body);
  return { box, body };
}

function paintBoard(data) {
  const host = $("plate-tiles");
  host.replaceChildren();
  (data.tiles || []).forEach((tile) => {
    const { box, body } = shellFor(tile);
    host.append(box);
    paintTile(body, tile);
  });
  $("plate-note").textContent = data.note || "";
  $("plate-summary").textContent = data.summary
    ? `${data.summary.dates.length} 日 ／ ${data.summary.rows} 行 ／ `
      + `停止 ${data.summary.stops} 行 ／ 読んだCSV ${data.summary.files} 本`
    : "";
}

function run() {
  const picked = chosen();
  if (!picked.length) {
    return toast.bad("plate-msg", "ラインを1つ以上選んでください");
  }
  $("plate-run").disabled = true;
  api("/api/plate/graph", { json: ask() }).then((result) => {
    $("plate-run").disabled = false;
    if (!result.ok) {
      $("plate-tiles").replaceChildren();
      return toast.bad("plate-msg", reason(result));
    }
    paintBoard(result.data);
    paintSkipped(result.data.summary);
    const rows = (result.data.summary || {}).rows || 0;
    if (!rows) {
      return toast.say("plate-msg", "その期間に読める集計がありませんでした");
    }
    toast.ok("plate-msg", `${rows} 行をまとめました`);
  }).catch(() => {
    $("plate-run").disabled = false;
    toast.bad("plate-msg", "読めませんでした");
  });
}

/** 書き出す。**ダウンロードはしません** ── 出力先フォルダへ書きます。 */
function save(kind) {
  const picked = chosen();
  if (!picked.length) {
    return toast.bad("plate-msg", "ラインを1つ以上選んでください");
  }
  api("/api/plate/export", { json: Object.assign(ask(), { kind }) })
    .then((result) => {
      if (!result.ok) { return toast.bad("plate-msg", reason(result)); }
      if (!result.data.ok) {
        paintSkipped(result.data.summary);
        return toast.say("plate-msg", result.data.message);
      }
      paintSkipped(result.data.summary);
      toast.ok("plate-msg", result.data.message
        + "(出力先フォルダ。「出力先を開く」で見られます)");
    });
}

export function wire() {
  $("plate-run").addEventListener("click", run);
  $("plate-csv").addEventListener("click", () => save("csv"));
  $("plate-xlsx").addEventListener("click", () => save("xlsx"));
  $("plate-reload").addEventListener("click", () => load());
  $("plate-all").addEventListener("click", () => {
    document.querySelectorAll("#plate-lines input:not([disabled])")
      .forEach((box) => { box.checked = true; });
  });
  // タブに入ったときに読み直します(設定を変えた直後でも追えるように)
  window.addEventListener("xlcsv:tab", (event) => {
    if (event.detail === "plate") { load(); }
  });
  return Promise.resolve();
}
