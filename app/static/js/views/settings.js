/*
  views/settings.js — 設定部

  どこを読んで、どこへ出すか。**実際に見に行く場所**と、そこがあるかを
  欄の下に出します(サーバが返したものをそのまま描くだけ)。
*/
import { api, reason } from "../api.js";
import * as toast from "../toast.js";

const FIELDS = ["input_dir", "output_dir", "line", "ref_dir", "stop_csv"];
const $ = (id) => document.getElementById(id);

function show(data) {
  FIELDS.forEach((key) => {
    if (document.activeElement !== $(key)) {
      $(key).value = (data.settings && data.settings[key]) || "";
    }
  });
  // 停止内訳が**どこから読めたか**。出どころが2つあるので、言わないと
  // 「名前が空のまま」の理由が分かりません
  const stops = $("stop-reasons");
  if (stops) {
    const found = data.stop_reasons;
    const text = (found && found.text) || "";
    stops.hidden = !text;
    stops.textContent = text;
    stops.className = "notice" + (found && found.ok ? "" : " warn");
  }
  (data.paths || []).forEach((item) => {
    const hint = $("hint-" + item.key);
    if (!hint) { return; }
    if (!item.text) { hint.textContent = ""; hint.className = "hint"; return; }
    hint.textContent = item.resolved + (item.note ? " ── " + item.note : "");
    hint.className = "hint " + (item.exists ? "ok" : (item.note ? "bad" : ""));
  });
}

export function load() {
  return api("/api/settings").then((result) => {
    if (result.ok) { show(result.data); }
  });
}

function save() {
  const payload = {};
  FIELDS.forEach((key) => { payload[key] = $(key).value; });
  api("/api/settings", { json: payload }).then((result) => {
    if (!result.ok) { return toast.bad("settings-msg", reason(result)); }
    show(result.data);
    toast.ok("settings-msg", "保存しました");
  });
}

function open(what) {
  api("/api/open", { json: { what } }).then((result) => {
    if (!result.ok) { toast.bad("settings-msg", reason(result)); }
  });
}

export function wire() {
  $("save").addEventListener("click", save);
  $("open-out").addEventListener("click", () => open("out"));
  $("open-logs").addEventListener("click", () => open("logs"));
  return load();
}
