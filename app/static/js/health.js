/*
  health.js — 心拍と接続の表示(基盤仕様書 2.9)

  画面が生きていることを一定の間隔で伝えます。途切れるとバックエンドは
  「誰も見ていない」と判断して終わります(`converter/idle_exit.py`)。

  **間隔はサーバが決めます**(`window.APP.heartbeat_ms`)。画面にも同じ数を
  書くと、片方を直したときに食い違い、自動終了が誤ります。

  【裏に回ったら、そう伝えます】(1.11.0)
  ブラウザは**見えていないタブのタイマーを間引きます**(Chrome は隠れて
  5分で1分に1回。Edge の「スリープ中のタブ」や Chrome のメモリセーバーは
  止めます)。Excel を全画面にした・窓を最小化した、だけで「見えていない」
  になり、止められたタブからは心拍が来ません ── サーバは30分で
  「誰も見ていない」と判断して終わっていました。

  だから**隠れる瞬間に `hidden` を送ります**(`sendBeacon`。この先ここの
  タイマーが動く保証はありません)。サーバは、裏に回ったタブは心拍が
  止まっても居るものとみなします。

  【戻ってきたことに、自分で気づきます】
  表に戻った・凍結が解けた・戻る/進むで戻った・スリープから起きた、の
  どれでも同じ `wake()` を走らせます: すぐ心拍を送り、占有を取り直し、
  接続の表示を直します。タイマー任せだと、戻ってから数秒間
  「応答がありません」のまま見えます(凍結明けはもっと遅い)。

  スリープは**タイマーの空き**で気づきます。5秒ごとの見張りが30秒以上
  空いて起きたら、そのあいだ止まっていたということです。見えているタブの
  ときだけ見ます ── 裏のタブは間引かれて空くのが当たり前なので。
*/
import { api } from "./api.js";
import * as screen from "./screen.js";

const LOST_AFTER = 3;          // 何回続けて届かなければ切れたと見るか

// スリープに気づくための見張りの間隔と、「止まっていた」とみなす空き(ms)
const WAKE_TICK_MS = 5000;
const WAKE_GAP_MS = 30000;

let missed = 0;
let timer = null;

// 閉じた合図を送ったあと。**凍結(`freeze`)の合図で取り消しません** ──
// 戻る/進むの控え(bfcache)に入るときは pagehide → freeze の順で来ます
let left = false;

// 読み込みごとのページ番号。**閉じたページから遅れて着いた合図**で、
// サーバが生き返らせたり、再読込した新しいページを落としたりしないための
// 印です(`converter/idle_exit.py`)。名札(tabId)は再読込でも同じなので
// 別に持ちます
function newPage() {
  return (crypto.randomUUID && crypto.randomUUID())
    || String(Date.now()) + Math.random().toString(16).slice(2);
}
let page = newPage();

function show(ok) {
  const box = document.getElementById("conn");
  box.className = ok ? "conn" : "conn lost";
  document.getElementById("conn-text").textContent =
    ok ? "接続中" : "応答がありません";
}

function visibility() {
  return document.visibilityState === "hidden" ? "hidden" : "visible";
}

/** 届けきってもらう(閉じる・隠れる瞬間は `fetch` が捨てられます)。 */
function beacon(body) {
  const payload = JSON.stringify(
    Object.assign({ tab: screen.tabId(), page }, body));
  if (navigator.sendBeacon
      && navigator.sendBeacon("/api/alive",
                              new Blob([payload], { type: "application/json" }))) {
    return;
  }
  // `sendBeacon` が使えない/断られたときは、切られてもよい要求で送ります
  api("/api/alive", { json: JSON.parse(payload), keepalive: true })
    .catch(() => { /* 届かなくても画面は続きます */ });
}

function beat() {
  if (left) { return; }
  // 裏に回っていても送ります(間引かれても届くぶんは届く)。**いまどちらか
  // を毎回言い添えます** ── 隠れた合図が届かなかったときの取り戻し
  api("/api/alive", { json: { tab: screen.tabId(), page,
                              state: visibility() } }).then((result) => {
    if (!result.ok) { return screen.lost(); }
    if (result.data.screen_ok === false) {
      // **裏に回っていただけなら、黙って取り直します。**
      // ほかの画面が使っていれば取り直せないので、そのときは覆いを出します
      return screen.regain().then((again) => {
        if (!again) { return screen.lost(); }
        missed = 0;
        show(true);
      });
    }
    missed = 0;
    show(true);
  }).catch(() => {
    missed += 1;
    show(false);
    if (missed >= LOST_AFTER) {
      screen.gate("バックエンドとの接続が切れました",
                  "アプリの本体が終了したか、応答しなくなりました。",
                  "変換中だった場合は、ログで最後の状態を確認できます。");
    }
  });
}

let lastWake = 0;

/** 戻ってきた。**すぐ心拍を送ります**(見張りの先回りを防ぐため)。 */
function wake(why) {
  left = false;
  const now = Date.now();
  // 表に戻る・凍結が解ける・回線が戻るは重なって来ます。1回に束ねます
  if (now - lastWake < 1000) { return; }
  lastWake = now;
  missed = 0;
  beat();
}

/** 裏に回った。**心拍が止まっても落とされないように。** */
function hide(why) {
  if (left) { return; }
  beacon({ state: "hidden", why });
}

export function startHeartbeat() {
  beat();
  timer = setInterval(beat, window.APP.heartbeat_ms || 3000);
  window.addEventListener("xlcsv:gated", () => clearInterval(timer));

  // 裏に回った / 表に戻った
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { return hide("hidden"); }
    if (screen.isActive()) { wake("visible"); }
  });
  // ブラウザがタブを凍結する/解く(Chrome の Page Lifecycle)。凍結の前には
  // たいてい隠れた合図が出ていますが、念のためもう1度
  document.addEventListener("freeze", () => hide("freeze"));
  document.addEventListener("resume", () => wake("resume"));
  // 戻る/進むで控えから戻った。閉じた合図を送ったページ番号は使えないので
  // **番号を取り直します**
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) { return; }
    page = newPage();
    wake("pageshow");
  });
  // 回線が戻った(アダプタの切り替え・スリープ明け)
  window.addEventListener("online", () => wake("online"));

  // スリープに気づく。**見えているあいだだけ**タイマーの空きで見ます
  let last = Date.now();
  setInterval(() => {
    const now = Date.now();
    const gap = now - last;
    last = now;
    if (gap > WAKE_GAP_MS && document.visibilityState === "visible") {
      wake("sleep");
    }
  }, WAKE_TICK_MS);
}

/** タブが閉じられたことを伝える。**待たずに終われるように。** */
export function markClosed() {
  left = true;
  beacon({ leaving: true });
}
