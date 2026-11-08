//! エクセル → CSV 変換器 デスクトップ版の外枠(Rust / Tauri)
//!
//! **業務の処理は1つも持ちません。** 変換・機側データ集計・設定・ログは
//! Python のままです(`bridge.py` → `app/` → `converter/` → `vendor/`)。
//! ここが持つのは OS の仕事だけです:
//!
//! - 窓(WebView2 / WebKitGTK)を出す
//! - Python を子として起こし、落ちたら理由を窓に出す
//! - 画面の要求(`app://localhost/…`)を Python の標準入力へ渡す(`bridge.rs`)
//! - 2つ目を起こさせない(`runtime/desktop.lock`)
//! - 終わるときに確かめる(変換中なら訊く)
//!
//! **ポートを使いません。** ブラウザ版のように 127.0.0.1 で待ち受けないので、
//! ファイアウォールの警告も、ポートの取り合いもありません。
//! ブラウザ版(`Start.vbs`)は予備としてそのまま残っています ──
//! **同時には動きません**(あとから開いたほうが止まります。判定は Python 側の
//! `converter/launch_marker.py`)。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bridge;
mod pages;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use bridge::{Bridge, Phase};
use tauri::http::{Request, Response};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind, MessageDialogResult};

/// 窓の名前(帯にも出ます)
const TITLE: &str = "エクセル → CSV 変換器";

/// 画面を読み込む独自の宛先。**ネットワークを通りません。**
const SCHEME: &str = "app";

/// 画面の宛先。Windows の WebView2 は `http://app.localhost/`、
/// それ以外(WebKitGTK)は `app://localhost/` になります
fn app_url(path: &str) -> tauri::Url {
    let base = if cfg!(windows) {
        format!("http://{SCHEME}.localhost")
    } else {
        format!("{SCHEME}://localhost")
    };
    format!("{base}{path}").parse().expect("宛先を組み立てられません")
}

fn is_app_url(url: &tauri::Url) -> bool {
    match url.scheme() {
        SCHEME => true,
        "http" | "https" => url.host_str().map(|h| h == format!("{SCHEME}.localhost")).unwrap_or(false),
        _ => false,
    }
}

/// ツール一式の置き場所。`bridge.py` が居るフォルダを、exe から上へたどって探します。
/// `XLCSV_ROOT` で指定もできます(開発中に `target/release` から動かすため)。
fn app_root() -> PathBuf {
    if let Ok(found) = std::env::var("XLCSV_ROOT") {
        let path = PathBuf::from(found);
        if path.join("bridge.py").is_file() {
            return path;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut at = exe.parent().map(Path::to_path_buf);
        while let Some(dir) = at {
            if dir.join("bridge.py").is_file() {
                return dir;
            }
            at = dir.parent().map(Path::to_path_buf);
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// ローカル領域(ログ・目印)。Python 側の `app_config.local_root()` と同じ決め方。
fn local_root(root: &Path) -> PathBuf {
    if let Ok(found) = std::env::var("XLCSV_LOCAL_DIR") {
        if !found.trim().is_empty() {
            return PathBuf::from(found);
        }
    }
    let name = std::fs::read_to_string(root.join("config").join("app.json"))
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|conf| {
            conf.get("local_dir_name")
                .and_then(|v| v.as_str())
                .map(str::to_string)
        })
        .unwrap_or_else(|| "ExcelCsvConverter".into());
    if let Ok(base) = std::env::var("LOCALAPPDATA") {
        return PathBuf::from(base).join(name);
    }
    if let Ok(base) = std::env::var("XDG_DATA_HOME") {
        return PathBuf::from(base).join(name);
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_default())
        .join(".local")
        .join("share")
        .join(name)
}

fn log_hint(root: &Path) -> String {
    local_root(root).join("logs").display().to_string()
}

/// **2つ目を起動させない**ための錠(OS のファイルロック)。取れたら握ったまま返します。
///
/// 多重起動の防止は `tauri-plugin-single-instance` がしますが、その仕組みは
/// OS ごとに違い(Linux は D-Bus)、使えない環境では黙って素通しになります。
/// 同じ出力先へ2つの Python が書きに行かないよう、ここでも止めます。
/// 錠はプロセスが終われば OS が外すので、落ちても残りません。
fn take_instance_lock(root: &Path) -> Option<std::fs::File> {
    let dir = local_root(root).join("runtime");
    let _ = std::fs::create_dir_all(&dir);
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join("desktop.lock"))
        .ok()?;
    match file.try_lock() {
        Ok(()) => Some(file),
        Err(_) => None,
    }
}

fn html(status: u16, body: String) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header("Content-Type", "text/html; charset=utf-8")
        .header("Cache-Control", "no-store")
        .body(body.into_bytes())
        .unwrap()
}

fn json_error(status: u16, reason: &str, detail: &str) -> Response<Vec<u8>> {
    let body = serde_json::json!({"ok": false, "reason": reason, "detail": detail});
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json; charset=utf-8")
        .body(serde_json::to_vec(&body).unwrap())
        .unwrap()
}

/// 画面からの要求1件を Python へ渡します。
fn handle(bridge: &Bridge, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let uri = request.uri().clone();
    let path = uri.path().to_string();
    let wants_page = request.method() == "GET"
        && !path.starts_with("/api/")
        && !path.starts_with("/static/");

    if bridge.phase() == Phase::Starting {
        // 画面なら少しだけ待ち、まだなら「起動しています」を出して読み直して
        // もらいます。画面以外(静的ファイル・API)は始まるまで待ちます
        let limit = if wants_page {
            Duration::from_millis(400)
        } else {
            Duration::from_secs(120)
        };
        if !bridge.wait_started(limit) && bridge.phase() == Phase::Starting {
            return html(200, pages::starting());
        }
    }
    if bridge.phase() != Phase::Started {
        if wants_page || path == "/" {
            return html(
                503,
                pages::failure(
                    bridge.failure().as_ref(),
                    &bridge.python(),
                    &bridge.stderr_tail(),
                    &log_hint(bridge.root()),
                ),
            );
        }
        return json_error(503, "本体が動いていません", "このツールを開き直してください");
    }

    if request.body().len() > bridge::MAX_BODY {
        return json_error(413, "大きすぎます", bridge::TOO_LARGE_MESSAGE);
    }

    let host = uri.host().unwrap_or("app.localhost").to_string();
    let mut headers: Vec<(String, String)> = request
        .headers()
        .iter()
        .filter_map(|(k, v)| Some((k.as_str().to_string(), v.to_str().ok()?.to_string())))
        .filter(|(k, _)| !k.eq_ignore_ascii_case("host"))
        .collect();
    headers.push(("Host".into(), host));

    match bridge.call(
        request.method().as_str(),
        &path,
        uri.query().unwrap_or(""),
        headers,
        request.body(),
    ) {
        Ok(reply) => {
            let mut builder = Response::builder().status(reply.status);
            for (k, v) in &reply.headers {
                // 長さと転送の方法は WebView が自分で決めます
                if k.eq_ignore_ascii_case("content-length")
                    || k.eq_ignore_ascii_case("transfer-encoding")
                {
                    continue;
                }
                builder = builder.header(k.as_str(), v.as_str());
            }
            builder
                .body(reply.body)
                .unwrap_or_else(|_| json_error(500, "応答を組み立てられません", ""))
        }
        Err(reason) => {
            if wants_page {
                html(
                    503,
                    pages::failure(
                        bridge.failure().as_ref(),
                        &bridge.python(),
                        &bridge.stderr_tail(),
                        &log_hint(bridge.root()),
                    ),
                )
            } else {
                json_error(503, "本体が答えません", &reason)
            }
        }
    }
}

// ------------------------------------------------------------------
// 画面から呼ぶもの(`app/static/js/desktop.js`)
// ------------------------------------------------------------------
/// 窓を閉じる。画面の「終了」の最後に呼びます(JS からは窓を閉じられません)。
#[tauri::command]
fn close_window(window: WebviewWindow) {
    let _ = window.close();
}

/// デスクトップ版かどうかを画面へ伝えます(`desktop.js` が見ます)。
#[tauri::command]
fn is_desktop() -> bool {
    true
}

// ------------------------------------------------------------------
// 終わり方
// ------------------------------------------------------------------
/// 終わり方を確かめている最中か。確かめの窓を出しているあいだに、もう一度
/// 「閉じて」が来ても(× を続けて押した・`stop.bat` が頼んだ)窓を重ねません。
static CONFIRMING: AtomicBool = AtomicBool::new(false);

/// × の確かめの窓のボタン。
const QUIT_YES: &str = "終了する";
const QUIT_NO: &str = "やめる";

/// × の確かめの窓で「終了する」を押したか。**はっきり「終了する」のときだけ真。**
///
/// 窓の答えの読み方を部品まかせにしません ── 窓が出せなかった・窓の × で
/// 閉じた・知らない答え は、どれも「やめる」(終わらない)。確かめが黙って
/// 「はい」になると、変換の途中で落ちて中途半端なCSVが残ります。
fn chose_quit(answer: &MessageDialogResult) -> bool {
    match answer {
        MessageDialogResult::Custom(label) => label == QUIT_YES,
        // ボタンの名前を出せない環境では OK / キャンセルで返ります
        MessageDialogResult::Ok => true,
        _ => false,
    }
}

/// 窓の × を押した。**画面の「終了」と同じ確認を通ります**(変換中なら訊く)。
fn confirm_close(app: AppHandle, bridge: Arc<Bridge>) {
    if CONFIRMING.swap(true, Ordering::SeqCst) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
        return;
    }
    thread::spawn(move || {
        let json = vec![("Content-Type".to_string(), "application/json".to_string())];
        let ask = bridge.call("POST", "/api/shutdown", "", json.clone(), b"{}");
        match ask {
            Ok(reply) if reply.status == 409 => {
                // 変換中。**訊いてから**にします(途中で落とすと半分書いた
                // CSVが残ります)
                let found = serde_json::from_slice::<serde_json::Value>(&reply.body).ok();
                let text = |key: &str| {
                    found
                        .as_ref()
                        .and_then(|v| v.get(key))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string()
                };
                let message = match (text("reason"), text("detail")) {
                    (reason, detail) if !reason.is_empty() => {
                        format!("{reason}\n\n{detail}")
                    }
                    _ => "変換中です。中止して終了しますか?".into(),
                };
                let answer = app
                    .dialog()
                    .message(message)
                    .title(TITLE)
                    .kind(MessageDialogKind::Warning)
                    .buttons(MessageDialogButtons::OkCancelCustom(
                        QUIT_YES.into(),
                        QUIT_NO.into(),
                    ))
                    .blocking_show_with_result();
                if chose_quit(&answer) {
                    let _ = bridge.call("POST", "/api/shutdown", "", json, br#"{"force": true}"#);
                    exit_soon(app);
                } else {
                    CONFIRMING.store(false, Ordering::SeqCst);
                }
            }
            // 200: Python が `quit` を知らせてきます。来なくても少し待って終えます
            _ => exit_soon(app),
        }
    });
}

fn exit_soon(app: AppHandle) {
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(1200));
        app.exit(0);
    });
}

fn main() {
    let root = app_root();
    let bridge = Bridge::new(root, bridge::new_token());

    let for_protocol = bridge.clone();
    let for_close = bridge.clone();
    let for_setup = bridge.clone();
    let for_exit = bridge.clone();

    let app = tauri::Builder::default()
        // 2つ目を開こうとしたら、開いている窓を前に出すだけ
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .register_asynchronous_uri_scheme_protocol(SCHEME, move |_ctx, request, responder| {
            let bridge = for_protocol.clone();
            // 要求ごとに別のスレッドで答えます(画面は心拍・進捗・操作を同時に出します)
            thread::spawn(move || responder.respond(handle(&bridge, request)));
        })
        .invoke_handler(tauri::generate_handler![close_window, is_desktop])
        .on_window_event(move |window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    confirm_close(window.app_handle().clone(), for_close.clone());
                }
            }
        })
        .setup(move |app| {
            match take_instance_lock(for_setup.root()) {
                Some(lock) => {
                    // 終わるまで握っておきます
                    Box::leak(Box::new(lock));
                }
                None => {
                    // すでに1つ動いています(窓を前に出すのは single-instance の仕事)
                    app.handle().exit(0);
                    return Ok(());
                }
            }
            let handle = app.handle().clone();
            for_setup.set_on_quit(move || handle.exit(0));
            let handle = app.handle().clone();
            for_setup.set_on_lost(move || {
                // 理由の画面を出します(読み直すと `handle` が失敗の画面を返します)
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.eval("location.reload()");
                }
            });
            // Python は別のスレッドで起こします。窓はすぐ出します
            let starter = for_setup.clone();
            thread::spawn(move || starter.start());

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(app_url("/")))
                .title(TITLE)
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                .center()
                // ブックのドラッグ&ドロップを画面(HTML)で受けるため、
                // Tauri がファイルの落下を横取りしないようにします
                .disable_drag_drop_handler()
                .on_navigation(|url| {
                    // アプリの外のページは窓の中で開きません
                    is_app_url(url)
                })
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("アプリを組み立てられませんでした");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // 標準入力を閉じて Python に終わってもらいます。終わらなければ止めます
            for_exit.shutdown(Duration::from_secs(8));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 画面の宛先はosで決まる() {
        let url = app_url("/api/health?x=1");
        assert!(is_app_url(&url));
        assert!(url.as_str().ends_with("/api/health?x=1"));
        // 社内サイトや外のページは窓の中で開かない
        assert!(!is_app_url(&"http://example.com/".parse().unwrap()));
        assert!(!is_app_url(&"https://nlm.example.jp/view".parse().unwrap()));
    }

    #[test]
    fn 終了の確かめは終了するを押したときだけ終わる() {
        assert!(chose_quit(&MessageDialogResult::Custom(QUIT_YES.into())));
        assert!(chose_quit(&MessageDialogResult::Ok));
        // やめる・窓の × で閉じた・知らない答え は、どれも終わらない
        assert!(!chose_quit(&MessageDialogResult::Custom(QUIT_NO.into())));
        assert!(!chose_quit(&MessageDialogResult::Cancel));
        assert!(!chose_quit(&MessageDialogResult::Custom("".into())));
    }

    #[test]
    fn ツールの置き場所と錠() {
        // 環境変数を触るので1つの試験にまとめます(試験は並んで走ります)
        let dir = std::env::temp_dir().join(format!("xlcsv_desktop_test_{}", std::process::id()));
        std::fs::create_dir_all(dir.join("config")).unwrap();
        std::fs::write(
            dir.join("config").join("app.json"),
            r#"{"local_dir_name": "変換器テスト"}"#,
        )
        .unwrap();
        std::env::remove_var("XLCSV_LOCAL_DIR");
        assert!(log_hint(&dir).contains("変換器テスト"));

        // `XLCSV_ROOT` は bridge.py が居るときだけ効く
        std::env::set_var("XLCSV_ROOT", &dir);
        assert_ne!(app_root(), dir, "bridge.py が無いフォルダは使わない");
        std::fs::write(dir.join("bridge.py"), "").unwrap();
        assert_eq!(app_root(), dir);
        std::env::remove_var("XLCSV_ROOT");

        std::env::set_var("XLCSV_LOCAL_DIR", dir.join("local"));
        let first = take_instance_lock(&dir);
        assert!(first.is_some(), "1つ目は錠を取れる");
        assert!(take_instance_lock(&dir).is_none(), "2つ目は取れない");
        drop(first);
        assert!(take_instance_lock(&dir).is_some(), "1つ目が終われば取れる");
        std::env::remove_var("XLCSV_LOCAL_DIR");
    }
}
