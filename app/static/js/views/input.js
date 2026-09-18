/*
  views/input.js — 読ませるブックを渡す

  ドラッグで放り込むか、設定した入力フォルダをそのまま読みます。
  フォルダごと放り込まれたら、中のブックを拾います。

  **100本を超えたぶんは送りません。** サーバも断りますが、送ってから
  断られるより、その場で言うほうが早い(サーバの上限が正)。
*/
import { api } from "../api.js";
import * as progress from "../progress.js";
import * as toast from "../toast.js";
import { setBusy } from "../busy.js";

const $ = (id) => document.getElementById(id);

export function folderSource() {
  return document.querySelector("input[name=source]:checked").value === "folder";
}

function filterBooks(files) {
  return files.filter((file) => {
    const name = (file.name || "").toLowerCase();
    return /\.(xlsx|xlsm)$/.test(name) && name.indexOf("~$") !== 0;
  });
}

function walk(entry) {
  if (entry.isFile) {
    return new Promise((resolve) => {
      entry.file((file) => resolve([file]), () => resolve([]));
    });
  }
  if (!entry.isDirectory) { return Promise.resolve([]); }
  const reader = entry.createReader();
  return new Promise((resolve) => {
    let found = [];
    const readMore = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          return Promise.all(found.map(walk)).then((lists) =>
            resolve([].concat.apply([], lists)));
        }
        found = found.concat(Array.prototype.slice.call(batch));
        readMore();
      }, () => resolve([]));
    };
    readMore();
  });
}

function collect(transfer) {
  const items = transfer.items;
  if (!items || !items.length || !items[0].webkitGetAsEntry) {
    return Promise.resolve(filterBooks(
      Array.prototype.slice.call(transfer.files || [])));
  }
  const entries = [];
  for (let i = 0; i < items.length; i += 1) {
    const entry = items[i].webkitGetAsEntry();
    if (entry) { entries.push(entry); }
  }
  return Promise.all(entries.map(walk)).then((lists) =>
    filterBooks([].concat.apply([], lists)));
}

export function refresh() {
  return api("/api/input").then((result) => {
    if (!result.ok) { return; }
    const limit = result.data.limit || window.APP.max_files;
    $("intake-count").textContent = result.data.count + " / " + limit + " 本";
    $("intake-count").className = "count" + (result.data.count >= limit ? " full" : "");
    $("intake-list").innerHTML = "";
    (result.data.names || []).forEach((name) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = name;
      $("intake-list").appendChild(chip);
    });
  });
}

async function receive(files) {
  if (!files.length) {
    return toast.bad("intake-msg", "読めるブックがありませんでした(.xlsx / .xlsm)");
  }
  const limit = window.APP.max_files || 100;
  let extra = 0;
  if (files.length > limit) {
    extra = files.length - limit;
    files = files.slice(0, limit);
  }
  files.sort((a, b) => a.name.localeCompare(b.name, "ja"));

  setBusy(true, { folderSource: folderSource() });
  progress.show("受け取り", 0, files.length, "");
  await api("/api/input/begin", { json: {} });

  const refused = [];
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i];
    progress.show("受け取り", i + 1, files.length, file.name);
    try {
      const result = await api("/api/input/add", {
        method: "POST",
        headers: { "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      if (result.data && result.data.ok === false && result.data.message) {
        refused.push(result.data.message);
      }
    } catch (e) {
      refused.push(file.name + ": 送れませんでした");
    }
  }
  progress.hide();
  setBusy(false, { folderSource: folderSource() });
  await refresh();

  let message = files.length + " 本を受け取りました";
  if (extra) {
    message += " ── " + limit + " 本を超えたぶん(" + extra + " 本)は受け取っていません";
  }
  if (refused.length) { message += " ／ " + refused.join(" ／ "); }
  toast.say("intake-msg", message, (extra || refused.length) ? "bad" : "ok");
}

export function wire() {
  const drop = $("drop");
  const picker = $("picker");

  document.querySelectorAll("input[name=source]").forEach((radio) => {
    radio.addEventListener("change", () => {
      drop.classList.toggle("off", folderSource());
      $("folder-line").hidden = !folderSource();
    });
  });

  ["dragenter", "dragover"].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      if (!drop.classList.contains("off")) { drop.classList.add("over"); }
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    drop.addEventListener(name, () => drop.classList.remove("over"));
  });
  // 画面の外で落として、ブラウザがブックを開いてしまうのを防ぐ
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    if (drop.classList.contains("off")) { return; }
    collect(event.dataTransfer).then(receive);
  });
  drop.addEventListener("click", () => {
    if (!drop.classList.contains("off")) { picker.click(); }
  });
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { picker.click(); }
  });
  picker.addEventListener("change", () => {
    receive(filterBooks(Array.prototype.slice.call(picker.files)));
    picker.value = "";
  });

  $("intake-clear").addEventListener("click", () => {
    api("/api/input/clear", { json: {} }).then(() => {
      refresh();
      toast.ok("intake-msg", "受け取ったぶんを消しました");
    });
  });

  $("scan").addEventListener("click", () => {
    api("/api/input/scan", { json: { folder: $("input_dir").value } })
      .then((result) => {
        toast.say("scan-msg", result.data.message || "",
                  result.data.ok ? "ok" : "bad");
      });
  });

  return refresh();
}
