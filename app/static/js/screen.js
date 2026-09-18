/*
  screen.js — 画面は1枚だけ

  2枚開けると、2つの画面が同じ作業状態を奪い合います。片方で「変換開始」、
  もう片方で「中止」。片方で保存した設定を、もう片方は古いまま表示する。
  **混乱のもとなので、開くこと自体をお断りします。**

  判定は2段:

    1. 同じブラウザの中   BroadcastChannel で「もう1枚いますか」と聞く
                          ── 複製タブは sessionStorage ごと複製されるので、
                             サーバ側の番号だけでは見分けられません
    2. サーバ             /api/screen/claim ── 別のブラウザや、URLを直接
                          打った場合はこちらで断ります

  断られた画面は覆いを出し、以後どのAPIも呼びません(番号を持ちません)。
  **再読込は断りません** ── 断ると、読み込みに失敗した画面を開き直せなく
  なります。タブは自分の `tab_id` を持ち歩くので、同じタブなら通ります。
*/
import { api, useScreen, whenLost } from "./api.js";

const CHANNEL_NAME = "xlcsv-screen";
const ASK_MS = 300;

const channel = ("BroadcastChannel" in window)
  ? new BroadcastChannel(CHANNEL_NAME) : null;
const loadId = Math.random().toString(36).slice(2) + Date.now().toString(36);

let active = false;

const $ = (id) => document.getElementById(id);

/** この画面は使ってよいか。 */
export function isActive() {
  return active;
}

/** 覆いを出して、操作を止める。**断るときは理由を必ず添えます。** */
export function gate(title, detail, note, options = {}) {
  active = false;
  useScreen("");
  $("gate-title").textContent = title;
  $("gate-detail").textContent = detail || "";
  $("gate-note").textContent = note || "";
  $("gate-mark").textContent = options.mark || "!";
  $("gate-retry").hidden = !!options.hideRetry;
  $("gate").hidden = false;
  $("main").hidden = true;
  document.body.classList.remove("booting");
  window.dispatchEvent(new CustomEvent("xlcsv:gated"));
}

/** 使えなくなった(別の画面に渡った・応答が途切れた)。 */
export function lost() {
  if (!active) { return; }
  gate("この画面は使えなくなりました",
       "別の画面で開かれたか、しばらく応答がありませんでした。"
       + "取り違えを防ぐため、この画面の操作は受け付けません。",
       "変換が動いていた場合は、そのまま続いています。");
}

function askOthers() {
  return new Promise((resolve) => {
    if (!channel) { return resolve(false); }
    let answered = false;
    channel.onmessage = (event) => {
      const message = event.data || {};
      if (message.type === "ping" && active) {
        channel.postMessage({ type: "pong", to: message.from });
      } else if (message.type === "pong" && message.to === loadId) {
        answered = true;
      }
    };
    channel.postMessage({ type: "ping", from: loadId });
    setTimeout(() => resolve(answered), ASK_MS);
  });
}

function tabId() {
  try {
    let found = sessionStorage.getItem("xlcsv-tab") || "";
    if (!found) {
      found = Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem("xlcsv-tab", found);
    }
    return found;
  } catch (e) {          // 保存が禁止されている端末でも開けるように
    return loadId;
  }
}

/** 使ってよいか確かめる。使えるなら `true`。 */
export async function claim() {
  whenLost(lost);
  if (await askOthers()) {
    gate("この画面は開けません",
         "同じアプリの画面が、すでにこのブラウザで開かれています。"
         + "2枚開くと、どちらの操作が効いているのか分からなくなるため、"
         + "開くことをお断りしています。",
         "先に開いている画面をお使いください。");
    return false;
  }
  let result;
  try {
    result = await api("/api/screen/claim",
                       { json: { tab_id: tabId(), load_id: loadId } });
  } catch (e) {
    gate("バックエンドへ繋がりません", "アプリの本体が動いていないようです。",
         "start.bat(診断用)で起動すると、コンソールに理由が出ます。");
    return false;
  }
  if (!result.ok) {
    gate(result.data.reason || "この画面は開けません", result.data.detail,
         "先に開いている画面を閉じると、"
         + (result.data.retry_after || 12) + "秒ほどで開けるようになります。");
    return false;
  }
  useScreen(result.data.screen_id);
  active = true;
  $("gate").hidden = true;
  $("main").hidden = false;
  document.body.classList.remove("booting");
  return true;
}

/** 閉じるときに空ける。**次に開くまで待たせないため。** */
export function release() {
  if (!active) { return; }
  api("/api/screen/release", { json: {}, keepalive: true });
  if (channel) { channel.postMessage({ type: "bye", from: loadId }); }
}

$("gate-retry").addEventListener("click", () => location.reload());
$("gate-close").addEventListener("click", () => {
  window.close();
  $("gate-note").textContent = "閉じられませんでした。このタブは手で閉じてください。";
});
