/*
  views/input.js — 読ませるものを渡す

  受け取り場所は**タブごとに分かれています**。

    変換タブ         .xlsx / .xlsm     日報のブック
    機側データ集計   .xlsx / .xlsm / .csv

  片方を片づけても、もう片方は残ります ── 「入れたはずのものが無い」を
  作らないためです。

  【なぜ中身を送るのか】
  ブラウザは、放り込まれたファイルの**本当の場所を教えません**。安全上の
  決まりで、社内アプリでも変えられません。渡されるのは中身だけなので、
  いったんこの端末のローカル領域へ写してから、フォルダを指定したときと
  同じ道で読みます(`converter/intake.py`)。

  **100本を超えたぶんは送りません。** サーバも断りますが、送ってから
  断られるより、その場で言うほうが早い(上限はサーバの値が正)。
*/
import { api } from "../api.js";
import * as progress from "../progress.js";
import * as toast from "../toast.js";
import { setBusy } from "../busy.js";

const $ = (id) => document.getElementById(id);

const ZONES = {
  convert: {
    kind: "convert", drop: "drop", picker: "picker", clear: "intake-clear",
    count: "intake-count", list: "intake-list", msg: "intake-msg",
    accept: /\.(xlsx|xlsm)$/i, label: ".xlsx / .xlsm",
  },
  tally: {
    kind: "tally", drop: "tally-drop", picker: "tally-picker",
    clear: "tally-intake-clear", count: "tally-intake-count",
    list: "tally-intake-list", msg: "tally-intake-msg",
    accept: /\.(xlsx|xlsm|csv)$/i, label: ".xlsx / .xlsm / .csv",
    // 変換すると1日に4本出ます。集計の材料になるのは集計明細で、
    // 停止内訳は記号→内訳名に使います。**残る2本は送りません** ──
    // 受け入れは100本までなので、材料にならないものに枠を使わせない
    // (名前を変えられていても、サーバ側が中身を見て理由を言います)
    skip: /^(集計|計算内容)_/,
    skipLabel: "集計・計算内容のCSV",
  },
};

export function folderSource() {
  const picked = document.querySelector("input[name=source]:checked");
  return !!picked && picked.value === "folder";
}

function filterFiles(zone, files) {
  return files.filter((file) => {
    const name = file.name || "";
    return zone.accept.test(name) && name.indexOf("~$") !== 0;
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
    return Promise.resolve(Array.prototype.slice.call(transfer.files || []));
  }
  const entries = [];
  for (let i = 0; i < items.length; i += 1) {
    const entry = items[i].webkitGetAsEntry();
    if (entry) { entries.push(entry); }
  }
  return Promise.all(entries.map(walk)).then((lists) =>
    [].concat.apply([], lists));
}

export function refresh(kind) {
  const zone = ZONES[kind];
  return api("/api/input?kind=" + zone.kind).then((result) => {
    if (!result.ok) { return; }
    const limit = result.data.limit || window.APP.max_files;
    $(zone.count).textContent = result.data.count + " / " + limit + " 本";
    $(zone.count).className = "count" + (result.data.count >= limit ? " full" : "");
    $(zone.list).innerHTML = "";
    (result.data.names || []).forEach((name) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = name;
      $(zone.list).appendChild(chip);
    });
  });
}

async function receive(zone, files) {
  files = filterFiles(zone, files);
  let passed = 0;
  if (zone.skip) {
    const keep = files.filter((file) => !zone.skip.test(file.name || ""));
    passed = files.length - keep.length;
    files = keep;
  }
  if (!files.length) {
    const why = passed
      ? zone.skipLabel + "だけでした(集計の材料は集計明細のCSVかブックです)"
      : "読めるものがありませんでした(" + zone.label + ")";
    return toast.bad(zone.msg, why);
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
  await api("/api/input/begin", { json: { kind: zone.kind } });

  const refused = [];
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i];
    progress.show("受け取り", i + 1, files.length, file.name);
    try {
      const result = await api("/api/input/add", {
        method: "POST",
        headers: {
          "X-Filename": encodeURIComponent(file.name),
          "X-Intake": zone.kind,
        },
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
  await refresh(zone.kind);

  let message = files.length + " 本を受け取りました";
  if (passed) {
    message += " ── " + zone.skipLabel + "(" + passed
      + " 本)は集計の材料にならないので送っていません";
  }
  if (extra) {
    message += " ── " + limit + " 本を超えたぶん(" + extra + " 本)は受け取っていません";
  }
  if (refused.length) { message += " ／ " + refused.join(" ／ "); }
  toast.say(zone.msg, message, (extra || refused.length) ? "bad" : "ok");
}

function wireZone(zone) {
  const drop = $(zone.drop);
  const picker = $(zone.picker);
  if (!drop) { return; }

  ["dragenter", "dragover"].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      if (!drop.classList.contains("off")) { drop.classList.add("over"); }
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    drop.addEventListener(name, () => drop.classList.remove("over"));
  });
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    if (drop.classList.contains("off")) { return; }
    collect(event.dataTransfer).then((files) => receive(zone, files));
  });
  drop.addEventListener("click", () => {
    if (!drop.classList.contains("off")) { picker.click(); }
  });
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { picker.click(); }
  });
  picker.addEventListener("change", () => {
    receive(zone, Array.prototype.slice.call(picker.files));
    picker.value = "";
  });
  $(zone.clear).addEventListener("click", () => {
    api("/api/input/clear", { json: { kind: zone.kind } }).then(() => {
      refresh(zone.kind);
      toast.ok(zone.msg, "受け取ったぶんを消しました");
    });
  });
}

export function wire() {
  // 画面の外で落として、ブラウザがブックを開いてしまうのを防ぐ
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  Object.keys(ZONES).forEach((kind) => wireZone(ZONES[kind]));

  document.querySelectorAll("input[name=source]").forEach((radio) => {
    radio.addEventListener("change", () => {
      $("drop").classList.toggle("off", folderSource());
      $("folder-line").hidden = !folderSource();
    });
  });
  $("scan").addEventListener("click", () => {
    api("/api/input/scan", { json: { folder: $("input_dir").value } })
      .then((result) => {
        toast.say("scan-msg", result.data.message || "",
                  result.data.ok ? "ok" : "bad");
      });
  });

  return Promise.all([refresh("convert"), refresh("tally")]);
}
