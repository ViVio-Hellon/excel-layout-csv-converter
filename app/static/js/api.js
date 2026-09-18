/*
  api.js — サーバとのやりとり

  JSが持つのは「呼ぶ」「受け取ったものを描く」だけ。**断る判断も文言も
  サーバが持ちます**(2枚目の画面・100本の上限・設定の不足)。

  付けるものは2つ:

    X-Tool-Token  起動ごとの合言葉。別のサイトや別プロセスから操作
                  されないため(`app/__init__.py` の守り3)
    X-Screen-Id   いま使っている画面の番号(同 4)
*/
const TOKEN = window.APP.token;
const SCREEN_HEADER = window.APP.screen_header || "X-Screen-Id";

let screenId = "";
let onLost = null;

/** 画面の番号を覚える(`screen.js` が受け取ったあとに呼ぶ)。 */
export function useScreen(id) {
  screenId = id || "";
}

/** 画面が無効になったときの呼び先を登録する。 */
export function whenLost(handler) {
  onLost = handler;
}

/**
 * 1回呼ぶ。`{status, ok, data}` を返す。
 *
 * **投げません。** 断りも結果のうちなので、呼んだ側が `ok` を見て
 * 文言を出します ── 例外にすると、断りの理由が握り潰されやすい。
 */
export function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  headers["X-Tool-Token"] = TOKEN;
  if (screenId) { headers[SCREEN_HEADER] = screenId; }
  let body = options.body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  return fetch(path, {
    method: options.method || (body !== undefined ? "POST" : "GET"),
    headers,
    body,
    cache: "no-store",
    keepalive: !!options.keepalive,
  }).then((response) =>
    response.json().catch(() => ({})).then((data) => {
      if (data && data.screen_lost && onLost) { onLost(); }
      return { status: response.status, ok: response.ok, data };
    })
  );
}

/** 断りを1行にする。**文言はサーバのものをそのまま出す。** */
export function reason(result) {
  const data = result.data || {};
  return [data.reason, data.detail].filter(Boolean).join(" ── ");
}
