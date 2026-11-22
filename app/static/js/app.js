/*
  app.js — 画面の起動

  ここは外枠の面倒だけを見ます。中身は `views/` 以下。

  順序に意味があります。**使ってよいと分かるまで、何も繋ぎません** ──
  2枚目の画面で設定を読んだり、変換を始められたりしないようにするためです。
*/
import { api, reason } from "./api.js";
import * as desktop from "./desktop.js";
import { markClosed, startHeartbeat } from "./health.js";
import * as screen from "./screen.js";
import * as tabs from "./tabs.js";
import * as toast from "./toast.js";
import * as settings from "./views/settings.js";
import * as input from "./views/input.js";
import * as plate from "./views/plate.js";
import * as job from "./views/job.js";

const $ = (id) => document.getElementById(id);

async function boot() {
  desktop.mark();                               // デスクトップ版なら印を付ける
  if (!await screen.claim()) { return; }        // 断られた画面はここで終わり

  document.querySelectorAll(".limit-text").forEach((el) => {
    el.textContent = window.APP.max_files;
  });
  ["intake-count", "tally-intake-count"].forEach((id) => {
    $(id).textContent = "0 / " + window.APP.max_files + " 本";
  });

  wireShell();
  await Promise.all([settings.wire(), input.wire(), job.wire(),
                     plate.wire()]);
  // **タブは最後に配線します。** `tabs.wire()` は「いまのタブ」を知らせる
  // ので、先に呼ぶと**誰も聞いていないうちに知らせが飛びます** ── 板集計を
  // はじめに出すようにしたとき、タブを一度も押さないとラインの一覧も期間も
  // 空のままになりました(`views/plate.js` がその知らせで読み込むため)
  tabs.wire();
  startHeartbeat();
}

function wireShell() {
  $("quit").addEventListener("click", () => quit(false));
  $("diag-toggle").addEventListener("click", showDiagnostics);
  window.addEventListener("pagehide", () => {
    markClosed();               // 閉じたことを伝える(待たずに終われる)
    screen.release();
  });
}

function quit(force) {
  api("/api/shutdown", { json: { force } }).then((result) => {
    if (result.status === 409 && result.data.running) {
      if (window.confirm(result.data.reason + "\n\n" + result.data.detail)) {
        return quit(true);
      }
      return;
    }
    if (!result.ok) { return toast.notice(reason(result), "bad"); }
    farewell();
  }).catch(farewell);
}

function farewell() {
  // **デスクトップ版は窓ごと閉じます。** 窓が残ると「終了したのに窓がある」
  // ことになります(JS からは `window.close()` で閉じられません)
  if (desktop.isDesktop()) {
    screen.gate("終了しました", "窓を閉じています…", "",
                { mark: "✓", hideRetry: true });
    desktop.closeWindow();
    return;
  }
  screen.gate("終了しました",
              "バックエンドを終了しました。このタブは閉じてかまいません。",
              "もう一度使うときは Start.vbs から起動してください。",
              { mark: "✓", hideRetry: true });
}

function showDiagnostics() {
  const box = $("diag");
  if (!box.hidden) { box.hidden = true; return; }
  api("/api/diagnostics").then((result) => {
    const data = result.data;
    box.textContent = [
      data.text,
      "Python        : " + data.python + " (" + data.executable + ")",
      "PID           : " + data.pid,
      "ログ          : " + (data.logs || {}).folder,
      "受け取り置き場: " + data.intake,
      data.config_error, data.version_problem, data.version_note,
    ].filter(Boolean).join("\n");
    box.hidden = false;
  });
}

boot();
