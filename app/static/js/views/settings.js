/*
  views/settings.js — 設定部

  どこを読んで、どこへ出すか。**実際に見に行く場所**と、そこがあるかを
  欄の下に出します(サーバが返したものをそのまま描くだけ)。
*/
import { api, reason } from "../api.js";
import * as toast from "../toast.js";

const FIELDS = ["input_dir", "output_dir", "line", "ref_dir", "stop_csv",
                "plate_dir", "log_dir"];
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
  // ログが指した場所へ出せなかったときは、**黙らせません**
  const logNote = $("log-note");
  if (logNote) {
    const why = (data.logs && data.logs.note) || "";
    logNote.hidden = !why;
    logNote.textContent = why;
  }
  showDistribution(data.distribution || {});
  (data.paths || []).forEach((item) => {
    const hint = $("hint-" + item.key);
    if (!hint) { return; }
    if (!item.text) { hint.textContent = ""; hint.className = "hint"; return; }
    // ログは**実際に書いている場所**を出します(指した先の下に、ツール名の
    // フォルダを1つ作って出すので、指した道のままではありません)
    const where = (item.key === "log_dir" && data.logs && data.logs.folder)
      ? data.logs.folder : item.resolved;
    hint.textContent = where + (item.note ? " ── " + item.note : "");
    hint.className = "hint " + (item.exists ? "ok" : (item.note ? "bad" : ""));
  });
}

/**
 * 配布設定の様子。**どの値が配布設定から来たのか**まで出します。
 *
 * 札を出すのは、「この端末で打った覚えがないのに値が入っている」を
 * 不思議なままにしないためです ── 配った値だと分かれば、直してよいか
 * (配り直されると戻るのか)の見当が付きます。
 */
function showDistribution(state) {
  const dir = $("dist-dir");
  if (!dir) { return; }
  dir.textContent = state.dir || "";
  $("dist-state").textContent = state.problem
    ? "── " + state.problem
    : (state.exists ? "── あります" : "── まだありません");
  const host = $("dist-items");
  host.replaceChildren();
  const came = state.from_distribution || [];
  (state.items || []).forEach((item) => {
    const row = document.createElement("label");
    row.className = "dist-item";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = item.key;
    box.checked = !!item.default && !!item.value;
    box.disabled = !item.value;             // 空の項目は配れません
    const name = document.createElement("b");
    name.textContent = item.label;
    const value = document.createElement("span");
    value.textContent = item.value || "(空)";
    row.append(box, name, value);
    if (came.includes(item.key)) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "配布設定の値";
      row.append(tag);
    }
    host.append(row);
  });
}

function distribute(what) {
  const items = Array.from(
    document.querySelectorAll("#dist-items input:checked")).map((b) => b.value);
  if (what === "forget"
      && !window.confirm("配布設定を消します(この端末の設定はそのままです)。"
                         + "よろしいですか?")) { return; }
  api("/api/distribution", { json: { what, items } }).then((result) => {
    const data = result.data || {};
    if (!result.ok) { return toast.bad("dist-msg", reason(result)); }
    show(data);
    const said = (data.distribution_result || {}).message || data.message || "";
    if (data.ok) { toast.ok("dist-msg", said, 8000); }
    else { toast.bad("dist-msg", said); }
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
  $("dist-export").addEventListener("click", () => distribute("export"));
  $("dist-reload").addEventListener("click", () => distribute("reload"));
  $("dist-forget").addEventListener("click", () => distribute("forget"));
  return load();
}
