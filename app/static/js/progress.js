/*
  progress.js — 進み具合の帯

  受け取り(ドラッグ投入)と変換の両方が使います。**同じ場所に出す**ので、
  いま何をしているのかが1か所を見れば分かります。
*/
const $ = (id) => document.getElementById(id);

export function show(phase, done, total, current) {
  $("progress").hidden = false;
  $("progress-phase").textContent = phase;
  $("progress-count").textContent = done + " / " + total + " 本";
  $("progress-fill").style.width =
    (total ? Math.round((done * 100) / total) : 0) + "%";
  $("progress-current").textContent = current || "";
}

export function time(text) {
  $("progress-time").textContent = text || "";
}

export function hide() {
  $("progress").hidden = true;
}
