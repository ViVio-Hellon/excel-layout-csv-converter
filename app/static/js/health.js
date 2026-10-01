/*
  health.js — 心拍と接続の表示(基盤仕様書 2.9)

  画面が生きていることを一定の間隔で伝えます。途切れるとバックエンドは
  「誰も見ていない」と判断して終わります(`converter/idle_exit.py`)。

  **間隔はサーバが決めます**(`window.APP.heartbeat_ms`)。画面にも同じ数を
  書くと、片方を直したときに食い違い、自動終了が誤ります。
*/
import { api } from "./api.js";
import * as screen from "./screen.js";

const LOST_AFTER = 3;          // 何回続けて届かなければ切れたと見るか

let missed = 0;
let timer = null;

function show(ok) {
  const box = document.getElementById("conn");
  box.className = ok ? "conn" : "conn lost";
  document.getElementById("conn-text").textContent =
    ok ? "接続中" : "応答がありません";
}

function beat() {
  api("/api/alive", { json: { closed: false } }).then((result) => {
    if (!result.ok) { return screen.lost(); }
    if (result.data.screen_ok === false) {
      // **裏に回っていただけなら、黙って取り直します。**
      // ブラウザは隠れたタブの心拍を間引く(数分で凍結する)ので、
      // 戻ったときに占有が期限切れになっていることがあります。
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

export function startHeartbeat() {
  beat();
  timer = setInterval(beat, window.APP.heartbeat_ms || 3000);
  window.addEventListener("xlcsv:gated", () => clearInterval(timer));
  // **表に戻った瞬間に1回打ちます。** タイマー任せだと、戻ってから
  // 数秒間「応答がありません」のまま見えます(凍結明けはもっと遅い)
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && screen.isActive()) { beat(); }
  });
}

/** タブが閉じられたことを伝える。**待たずに終われるように。** */
export function markClosed() {
  api("/api/alive", { json: { closed: true }, keepalive: true });
}
