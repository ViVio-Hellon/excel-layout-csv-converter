/*
  toast.js — 画面に出す一言

  **文言はサーバが持ちます。** ここは出す場所と色を決めるだけです。
*/
export function say(id, text, kind = "") {
  const box = document.getElementById(id);
  if (!box) { return; }
  box.textContent = text || "";
  box.className = "msg" + (kind ? " " + kind : "");
}

export function ok(id, text, clearAfter = 4000) {
  say(id, text, "ok");
  if (clearAfter) { setTimeout(() => say(id, ""), clearAfter); }
}

export function bad(id, text) {
  say(id, text, "bad");
}

/** 変換の帯に出す知らせ(結果・失敗)。 */
export function notice(text, kind = "") {
  const box = document.getElementById("job-msg");
  box.hidden = !text;
  box.textContent = text || "";
  box.className = "notice" + (kind ? " " + kind : "");
}
