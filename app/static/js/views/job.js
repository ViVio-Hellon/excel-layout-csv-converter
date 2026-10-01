/*
  views/job.js — 走らせる(変換 / 機側データ集計)

  **走るのは一度に1つだけ**なので、進捗と結果はどちらのタブでも同じ場所に
  出します。中身はサーバが持っていて(`converter/jobs.py`)、ここは見に行って
  描くだけです ── 開き直しても、閉じているあいだに進んだぶんがそのまま出ます。

  印は3つ(変換のとき)。**合わなかったものを黙って通しません。**

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

//: 仕事の種類。サーバの `converter/jobs.py` と同じ文字
const TALLY = "機側データ集計";

const HEADS = {
  convert: ["", "ファイル", "日付", "ライン", "ページ", "行", "突き合わせ"],
  tally: ["", "ファイル", "日付", "種類", "拾った行", "理由・注記"],
};

//: 右そろえにする列(数が縦にそろうように)
const NUMS = { convert: [4, 5], tally: [4] };

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
  const tally = job.kind === TALLY;
  setBusy(job.running, { folderSource: folderSource() });
  $("idle-msg").hidden = true;
  $("running-title").textContent = job.kind + " ── 進み具合";

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
                 + (job.dry_run || !job.out_dir ? "" : "\n出力先: " + job.out_dir)
                 + tallyPaths(job),
                 kind);
    if (job.id !== lastId) {
      lastId = job.id;
      refreshIntake(tally ? "tally" : "convert");
      loadSettings();          // 出力先は書いたあとに出来ているので見直す
    }
  }
  if (job.master_notice && !job.master_ok && !tally) {
    toast.bad("scan-msg", job.master_notice);
  }
  renderResult(job, tally);
}

function renderResult(job, tally) {
  if (!job.files.length) { $("result-card").hidden = true; return; }
  $("result-card").hidden = false;
  $("result-note").textContent = tally
    ? "拾った行と、飛ばしたものの理由です。CSVは1本にまとめて出しています。"
    : "○ 読めて数も合った ／ ! 読めたが数が合わない(座標がずれているおそれが"
      + "あります) ／ × 読めなかった";

  const counts = job.counts;
  const tiles = tally
    ? [["読んだもの", counts.read_ok + " / " + counts.files, ""],
       ["拾った行", job.tally_rows, ""],
       ["飛ばしたもの", counts.bad, counts.bad ? "warn" : ""],
       [job.dry_run ? "書いたCSV(確認のみ)" : "書いたCSV", job.written, ""]]
    : [["読めた", counts.read_ok + " / " + counts.files, ""],
       ["数が合った", counts.verified, ""],
       ["数が合わない", counts.suspect, counts.suspect ? "warn" : ""],
       ["読めなかった", counts.bad, counts.bad ? "bad" : ""],
       [job.dry_run ? "書いたCSV(確認のみ)" : "書いたCSV", job.written, ""]];

  $("tiles").innerHTML = "";
  tiles.forEach((item) => {
    const tile = document.createElement("div");
    tile.className = "tile " + item[2];
    tile.innerHTML = "<b></b><span></span>";
    tile.querySelector("b").textContent = item[1];
    tile.querySelector("span").textContent = item[0];
    $("tiles").appendChild(tile);
  });

  const head = $("result-head");
  head.innerHTML = "";
  const nums = tally ? NUMS.tally : NUMS.convert;
  (tally ? HEADS.tally : HEADS.convert).forEach((label, index) => {
    const th = document.createElement("th");
    th.textContent = label;
    if (nums.indexOf(index) >= 0) { th.className = "num"; }
    head.appendChild(th);
  });

  const body = $("result").querySelector("tbody");
  body.innerHTML = "";
  job.files.forEach((file) => {
    const row = document.createElement("tr");
    row.className = !file.ok ? "ng" : (file.verify_ok ? "ok" : "suspect");
    const cells = tally
      ? [[file.mark, "mark"], [file.name, ""], [file.report_date, ""],
         [file.verify, ""], [file.rows || "", "num"],
         [file.error || file.note || "", "detail"]]
      : [[file.mark, "mark"], [file.name, ""], [file.report_date, ""],
         [(file.line_text ? file.line_text + "→" : "") + file.line, ""],
         [file.pages || "", "num"], [file.rows || "", "num"],
         [detail(file), "detail"]];
    cells.forEach((cell) => {
      const td = document.createElement("td");
      td.className = cell[1];
      td.textContent = cell[0];
      row.appendChild(td);
    });
    body.appendChild(row);
  });
}

// 出した集計CSVの道。**ラインごとに1本**出るので、全部並べます
// (機側には板に無い欄があるので分けています)
function tallyPaths(job) {
  const made = (job.tally_paths && job.tally_paths.length)
    ? job.tally_paths : (job.tally_path ? [job.tally_path] : []);
  if (!made.length) { return ""; }
  return "\n機側データ集計: " + made.join("\n" + " ".repeat(14));
}

// 詳細欄。**読むときに手を入れたこと(note)は必ず出します** ──
// 作業日の書き方を直した、など。黙って直したことにしません
function detail(file) {
  if (!file.ok) { return file.error || ""; }
  const marks = file.marks && file.marks.length
    ? file.marks.join(" ／ ") : (file.verify || "");
  return file.note ? (marks ? marks + " ／ ※" + file.note : "※" + file.note)
    : marks;
}

function begin(payload) {
  toast.notice("");
  api("/api/job/start", { json: payload }).then((result) => {
    if (!result.ok) { return toast.notice(reason(result), "bad"); }
    render(result.data.job);
    watch();
  });
}

function cancel() {
  if (!window.confirm("中止しますか?\n\n"
      + "読み取りの途中なら止まります。CSVは書きません。")) { return; }
  api("/api/job/cancel", { json: {} }).then((result) => {
    if (result.data && result.data.job) { render(result.data.job); }
  });
}

export function wire() {
  $("start").addEventListener("click", () => begin({
    tab: "convert",
    source: folderSource() ? "folder" : "intake",
    dry_run: $("dry_run").checked,
  }));
  $("tally-start").addEventListener("click", () => begin({
    tab: "tally", dry_run: $("tally_dry_run").checked,
    any_sheet: $("tally_any_sheet").checked }));
  $("cancel").addEventListener("click", cancel);
  // 閉じているあいだに走っていた仕事があれば、そのまま引き継ぐ
  return api("/api/job").then((result) => {
    if (result.ok && result.data.job && result.data.job.id) {
      render(result.data.job);
      if (result.data.job.running) { watch(); }
    }
  });
}
