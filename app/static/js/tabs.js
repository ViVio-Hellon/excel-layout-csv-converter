/*
  tabs.js — 変換 / 機側データ集計

  画面は1枚のままで、**やることだけ**を分けます。設定部と、進捗・結果は
  共通です ── 走る仕事は一度に1つなので、どちらのタブから始めても
  同じ場所に出ます。

  選んだタブは端末に覚えます(次に開いたとき同じタブが出ます)。覚えられない
  設定の端末でも動くよう、失敗は黙って捨てます。
*/
const KEY = "xlcsv-tab";

let current = "convert";

export function active() {
  return current;
}

export function show(name) {
  current = name;
  document.querySelectorAll(".tab").forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.hidden = panel.id !== "panel-" + name;
  });
  try { localStorage.setItem(KEY, name); } catch (e) { /* 覚えられなくてよい */ }
  window.dispatchEvent(new CustomEvent("xlcsv:tab", { detail: name }));
}

export function wire() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => show(tab.dataset.tab));
  });
  let saved = "";
  try { saved = localStorage.getItem(KEY) || ""; } catch (e) { saved = ""; }
  show(saved === "tally" ? "tally" : "convert");
}
