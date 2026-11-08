/*
  desktop.js — デスクトップ版(Tauri)のときだけ効く、窓まわりの世話

  **画面の中身は同じです。** ブラウザ版と同じHTML・同じJSで動きます。
  違うのは「窓」が要るところだけ:

    終了したあと … JS から窓は閉じられません(`window.close()` は効かない)。
                   外枠(Rust)に `close_window` を頼みます
    覆いの「閉じる」… 同じ理由で外枠に頼みます

  判定は `window.__TAURI__` があるかどうかだけ。ブラウザ版では何もしません
  (読み込まれても、関数が何もせずに戻ります)。
*/

/** デスクトップ版の窓の中にいるか。 */
export function isDesktop() {
  return !!(window.__TAURI__ && window.__TAURI__.core
            && typeof window.__TAURI__.core.invoke === "function");
}

/** 外枠に頼む(いないときは何もしない)。 */
function ask(command, args) {
  if (!isDesktop()) { return Promise.resolve(false); }
  return window.__TAURI__.core.invoke(command, args || {})
    .then(() => true)
    .catch(() => false);
}

/**
 * 窓を閉じる。**終了したあとだけ**呼びます。
 *
 * ブラウザ版はタブを閉じるのが利用者の仕事(`window.close()` は
 * 自分で開いたタブしか閉じられません)。デスクトップ版は窓が残ると
 * 「終了したのに窓がある」になるので、外枠に閉じてもらいます。
 */
export function closeWindow() {
  return ask("close_window");
}

/**
 * **落とされたファイルの本当の場所**を受け取る(デスクトップ版だけ)。
 *
 * ブラウザは本当の場所を教えてくれないので、中身をコピーして送って
 * いました(1本 50MB まで)。窓(Tauri)は場所を教えてくれるので、
 * **写さずにその場で読めます** ── コピーも上限もありません。
 *
 * `onDrop(paths)` に本当の場所の配列を渡します。`onOver(true/false)` は
 * 枠の見た目(入ってきた・出ていった)。
 */
export function whenFilesDropped(onDrop, onOver) {
  if (!isDesktop() || !window.__TAURI__.event) { return Promise.resolve(false); }
  const { listen } = window.__TAURI__.event;
  const over = (yes) => { if (onOver) { onOver(yes); } };
  return Promise.all([
    listen("tauri://drag-enter", () => over(true)),
    listen("tauri://drag-over", () => over(true)),
    listen("tauri://drag-leave", () => over(false)),
    listen("tauri://drag-drop", (event) => {
      over(false);
      const paths = (event.payload && event.payload.paths) || [];
      if (paths.length) { onDrop(paths); }
    }),
  ]).then(() => true).catch(() => false);
}

/** 画面に「デスクトップ版です」と印を付ける(帯の見た目を変えるため)。 */
export function mark() {
  if (isDesktop()) {
    document.body.classList.add("desktop");
  }
  return isDesktop();
}
