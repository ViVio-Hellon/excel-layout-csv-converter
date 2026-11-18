/*
  tabs.js — 変換 / 機側データ集計 / 板集計 / 設定

  画面は1枚のままで、**やることだけ**を分けます。進捗・結果は共通です
  ── 走る仕事は一度に1つなので、どちらのタブから始めても同じ場所に
  出ます(板集計と設定のタブでは隠します。隠す判断は `layout.css` が
  `body[data-tab]` を見て決めます)。

  **設定もタブの1つにしました。** 前は設定部を上に固定していたので、
  開くたびに設定が最初に目に入り、やることの画面が下へ送られていました。

  選んだタブは端末に覚えます(次に開いたとき同じタブが出ます)。覚えられない
  設定の端末でも動くよう、失敗は黙って捨てます。
*/
const KEY = "xlcsv-tab";

/** 出せるタブ。**ここに無い名前は受けません**(覚えていた字が古い場合) */
const NAMES = ["convert", "tally", "plate", "settings"];

/**
 * はじめて開いたときのタブ。
 *
 * **いちばんよく開くもの**にします ── 変換は溜まったブックを入れる
 * ときだけ、設定は据え付けのときだけです。毎日見るのは板集計なので、
 * 開いた瞬間にそこが出ているほうが手数が1つ減ります。
 *
 * 2度目からは**前に開いていたタブ**です(端末に覚えます)。
 */
const FIRST = "plate";

let current = FIRST;

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
  // いまのタブを `body` にも書きます ── CSS が「板集計と設定では
  // 進捗を隠す」を決めるのに使います(JSで隠すと、走っている間の
  // 出し入れが2か所に分かれます)
  document.body.dataset.tab = name;
  try { localStorage.setItem(KEY, name); } catch (e) { /* 覚えられなくてよい */ }
  window.dispatchEvent(new CustomEvent("xlcsv:tab", { detail: name }));
}

export function wire() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => show(tab.dataset.tab));
  });
  let saved = "";
  try { saved = localStorage.getItem(KEY) || ""; } catch (e) { saved = ""; }
  show(NAMES.includes(saved) ? saved : FIRST);
}
