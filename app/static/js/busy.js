/*
  busy.js — 変換中は触らせない

  設定と入力を変換の途中で変えられると、いま走っているものが何を
  読んでいるのか説明できなくなります。サーバも断りますが
  (`/api/settings` など)、**押せてしまうこと自体をなくします。**
*/
const FIELDS = ["input_dir", "output_dir", "line", "ref_dir"];

let busy = false;

export function isBusy() {
  return busy;
}

export function setBusy(value, { folderSource = false } = {}) {
  busy = value;
  const off = (id, disabled) => {
    const el = document.getElementById(id);
    if (el) { el.disabled = disabled; }
  };
  off("start", value);
  off("save", value);
  off("intake-clear", value);
  FIELDS.forEach((id) => off(id, value));
  const cancel = document.getElementById("cancel");
  if (cancel) { cancel.hidden = !value; }
  const drop = document.getElementById("drop");
  if (drop) { drop.classList.toggle("off", value || folderSource); }
}
