/*
  views/job.js — 変換

  進み具合はサーバが持っています(`converter/jobs.py`)。ここは見に行って
  描くだけです ── 開き直しても、閉じているあいだに進んだぶんがそのまま
  出ます。

  印は3つ。**合わなかったものを黙って通しません。**

    ○ 読めて、シート自身の合計とも合った
    ! 読めたが数が合わない(座標がずれているおそれ)
    × 読めなかった
*/
import { api, reason } from "../api.js";
import * as progress from "../progress.js";
import * as toast from "../toast.js";
import { setBusy } from "../busy.js";
import { folderSource, refresh as refreshIntake } from "./input.js";
import { load as loadSettings } from "./settings.js";

const $ = (id) => document.getElementById(id);

let poller = null;
let lastId = "";

function watch() {
  clearInterval(poller);
  poller = setInterval(() => {
    api("/api/job").then((result) => {
      if (result.ok) { render(result.data.job); }
    });
  }, window.APP.job_poll_ms || 400);
}

export function render(job) {
  if (!job || !job.id) { return; }
  setBusy(job.running, { folderSource: folderSource() });
  if (job.running) {
    progress.show(job.phase, job.done, job.total, job.current);
    let text = job.elapsed + " 秒経過";
    if (job.remaining !== null && job.remaining !== undefined) {
      text += " ／ あと " + job.remaining + " 秒くらい";
    }
    progress.time(text);
    toast.notice(job.message || "");
  } else {
    clearInterval(poller);
    poller = null;
    progress.show(job.phase || "終わりました", job.done, job.total, "");
    progress.time(job.elapsed + " 秒かかりました");
    const kind = job.counts.bad ? "bad" : (job.counts.suspect ? "warn" : "");
    toast.notice(job.state + " ── " + (job.message || "")
                 + (job.error ? " / " + job.error : "")
                 + (job.dry_run || !job.out_dir ? "" : "  出力先: " + job.out_dir),
                 kind);
    if (job.id !== lastId) {
      lastId = job.id;
      refreshIntake();
      loadSettings();          // 出力先は書いたあとに出来ているので見直す
    }
  }
  if (job.master_notice && !job.master_ok) {
    toast.bad("scan-msg", job.master_notice);
  }
  renderResult(job);
}

function renderResult(job) {
  if (!job.files.length) { $("result-card").hidden = true; return; }
  $("result-card").hidden = false;

  const counts = job.counts;
  $("tiles").innerHTML = "";
  [["読めた", counts.read_ok + " / " + counts.files, ""],
   ["数が合った", counts.verified, ""],
   ["数が合わない", counts.suspect, counts.suspect ? "warn" : ""],
   ["読めなかった", counts.bad, counts.bad ? "bad" : ""],
   [job.dry_run ? "書いたCSV(確認のみ)" : "書いたCSV", job.written, ""],
  ].forEach((item) => {
    const tile = document.createElement("div");
    tile.className = "tile " + item[2];
    tile.innerHTML = "<b></b><span></span>";
    tile.querySelector("b").textContent = item[1];
    tile.querySelector("span").textContent = item[0];
    $("tiles").appendChild(tile);
  });

  const body = $("result").querySelector("tbody");
  body.innerHTML = "";
  job.files.forEach((file) => {
    const row = document.createElement("tr");
    row.className = !file.ok ? "ng" : (file.verify_ok ? "ok" : "suspect");
    const detail = file.ok
      ? (file.marks && file.marks.length ? file.marks.join(" ／ ") : file.verify)
      : file.error;
    [[file.mark, "mark"], [file.name, ""], [file.report_date, ""],
     [(file.line_text ? file.line_text + "→" : "") + file.line, ""],
     [file.pages || "", "num"], [file.rows || "", "num"],
     [detail || "", "detail"],
    ].forEach((cell) => {
      const td = document.createElement("td");
      td.className = cell[1];
      td.textContent = cell[0];
      row.appendChild(td);
    });
    body.appendChild(row);
  });
}

function start() {
  toast.notice("");
  api("/api/job/start", {
    json: { source: folderSource() ? "folder" : "intake",
            dry_run: $("dry_run").checked },
  }).then((result) => {
    if (!result.ok) { return toast.notice(reason(result), "bad"); }
    render(result.data.job);
    watch();
  });
}

function cancel() {
  if (!window.confirm("変換を中止しますか?\n\n"
      + "読み取りの途中なら止まります。CSVは書きません。")) { return; }
  api("/api/job/cancel", { json: {} }).then((result) => {
    if (result.data && result.data.job) { render(result.data.job); }
  });
}

export function wire() {
  $("start").addEventListener("click", start);
  $("cancel").addEventListener("click", cancel);
  // 閉じているあいだに走っていた変換があれば、そのまま引き継ぐ
  return api("/api/job").then((result) => {
    if (result.ok && result.data.job && result.data.job.id) {
      render(result.data.job);
      if (result.data.job.running) { watch(); }
    }
  });
}
