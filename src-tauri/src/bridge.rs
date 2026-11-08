//! Python とのやりとり ── **ポートを使わない**
//!
//! 外枠(この exe)が `bridge.py` を**子として起動**し、標準入出力で
//! 1件ずつやりとりします。ソケットは1つも開きません。
//!
//! ```text
//! 画面(WebView) ──app://localhost/…──▶ main.rs ──▶ ここ ──標準入力──▶ bridge.py
//!                                                      ◀──標準出力──
//! ```
//!
//! やりとりの形は `bridge.py` の説明と同じです ── 1件 = 見出し1行(JSON)
//! + 本文(見出しの `len` バイト)。本文を JSON に埋めないのは、ブック1冊が
//! 数十MBになるためです。
//!
//! 知らせ(`event`)は3つ: `started`(準備ができた)/ `quit`(終わってよい)/
//! `fatal`(起動できない。理由を窓に出す)。

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// 標準エラーの末尾を、これだけ覚えておきます(理由の画面に出すため)
const STDERR_LINES: usize = 60;

/// 1件の本文の上限。**`bridge.py` の `MAX_BODY_MARGIN` と同じ考え方**で、
/// ブック1冊の上限(50MB)に余裕を足したもの。片方だけ直すと、片方が
/// 黙って切ります
pub const MAX_BODY: usize = 64 * 1024 * 1024;

pub const TOO_LARGE_MESSAGE: &str =
    "1件で送れる大きさを超えました。フォルダを指して「入力フォルダをそのまま読む」でお試しください";

/// 1件の応答を待つ上限。変換は100本で数秒ですが、共有フォルダが遅いと
/// 伸びます ── **待ち切れずに切るより、待つほうがまし**
const REPLY_TIMEOUT: Duration = Duration::from_secs(600);

#[derive(Clone, Debug)]
pub struct Reply {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

/// 起動できなかった理由(窓に出します)
#[derive(Clone, Debug, Default)]
pub struct Failure {
    pub message: String,
    pub hint: String,
    pub log_dir: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// 起こしている最中(まだ何も言ってこない)
    Starting,
    /// 受け付けている
    Started,
    /// 終わった・起動できなかった
    Ended,
}

struct State {
    phase: Phase,
    failure: Option<Failure>,
    python: String,
}

pub struct Bridge {
    root: PathBuf,
    token: String,
    state: Mutex<State>,
    changed: Condvar,
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    pending: Mutex<HashMap<u64, mpsc::Sender<Reply>>>,
    stderr: Arc<Mutex<std::collections::VecDeque<String>>>,
    next_id: AtomicU64,
    on_quit: Mutex<Option<Box<dyn Fn() + Send + Sync>>>,
    on_lost: Mutex<Option<Box<dyn Fn() + Send + Sync>>>,
}

impl Bridge {
    pub fn new(root: PathBuf, token: String) -> Arc<Self> {
        Arc::new(Self {
            root,
            token,
            state: Mutex::new(State {
                phase: Phase::Starting,
                failure: None,
                python: String::new(),
            }),
            changed: Condvar::new(),
            stdin: Mutex::new(None),
            child: Mutex::new(None),
            pending: Mutex::new(HashMap::new()),
            stderr: Arc::new(Mutex::new(std::collections::VecDeque::new())),
            next_id: AtomicU64::new(1),
            on_quit: Mutex::new(None),
            on_lost: Mutex::new(None),
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn set_on_quit(&self, f: impl Fn() + Send + Sync + 'static) {
        *self.on_quit.lock().unwrap() = Some(Box::new(f));
    }

    pub fn set_on_lost(&self, f: impl Fn() + Send + Sync + 'static) {
        *self.on_lost.lock().unwrap() = Some(Box::new(f));
    }

    pub fn phase(&self) -> Phase {
        self.state.lock().unwrap().phase
    }

    pub fn failure(&self) -> Option<Failure> {
        self.state.lock().unwrap().failure.clone()
    }

    pub fn python(&self) -> String {
        self.state.lock().unwrap().python.clone()
    }

    pub fn stderr_tail(&self) -> Vec<String> {
        self.stderr.lock().unwrap().iter().cloned().collect()
    }

    /// 受け付けが始まるまで待つ(窓は先に出ているので、ここで少しだけ待てます)
    pub fn wait_started(&self, limit: Duration) -> bool {
        let deadline = Instant::now() + limit;
        let mut state = self.state.lock().unwrap();
        while state.phase == Phase::Starting {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return false;
            }
            let (next, _timeout) = self.changed.wait_timeout(state, left).unwrap();
            state = next;
        }
        state.phase == Phase::Started
    }

    fn set_phase(&self, phase: Phase, failure: Option<Failure>) {
        {
            let mut state = self.state.lock().unwrap();
            state.phase = phase;
            if failure.is_some() {
                state.failure = failure;
            }
        }
        self.changed.notify_all();
    }

    // --------------------------------------------------------------
    // 起こす
    // --------------------------------------------------------------
    pub fn start(self: &Arc<Self>) {
        let script = self.root.join("bridge.py");
        if !script.is_file() {
            self.set_phase(
                Phase::Ended,
                Some(Failure {
                    message: format!("bridge.py が見つかりません: {}", script.display()),
                    hint: "この exe は、ツール一式が入ったフォルダの中に置いてください\
                           (ショートカットを作るのは大丈夫です)。"
                        .into(),
                    log_dir: String::new(),
                }),
            );
            return;
        }
        let mut tried = Vec::new();
        for (program, args) in python_candidates() {
            tried.push(program.clone());
            if self.spawn(&program, &args, &script).is_err() {
                continue; // 見つからない。次の候補へ
            }
            // 1行目(started / fatal)か、何も言わずに終わるのを待ちます
            let mut state = self.state.lock().unwrap();
            while state.phase == Phase::Starting {
                state = self.changed.wait(state).unwrap();
            }
            let spoke = state.phase == Phase::Started || state.failure.is_some();
            drop(state);
            if spoke {
                return;
            }
            self.state.lock().unwrap().phase = Phase::Starting;
        }
        self.set_phase(
            Phase::Ended,
            Some(Failure {
                message: "Python が見つかりません(または起動できません)".into(),
                hint: format!(
                    "この端末の Python を使います(同梱していません)。\n\
                     Python 3.11 以上を入れて、インストーラの最初の画面で\
                     「Add python.exe to PATH」にチェックを入れてください。\n\
                     **追加ライブラリは要りません**(標準ライブラリだけで動きます)。\n\n\
                     試したもの: {}",
                    tried.join(" / ")
                ),
                log_dir: String::new(),
            }),
        );
    }

    fn spawn(self: &Arc<Self>, program: &str, args: &[String], script: &Path) -> std::io::Result<()> {
        let mut command = Command::new(program);
        command
            .args(args)
            .arg("-X")
            .arg("utf8")
            .arg(script)
            .current_dir(&self.root)
            // **合言葉は外枠が決めて、Python へ渡します。** 画面のHTMLに
            // 入る `window.APP.token` も同じものになり、画面から来た要求を
            // そのまま通せます(渡さないと、画面が自分の画面を受け取れません)
            .env("XLCSV_TOKEN", &self.token)
            // 窓(この exe)の番号。`stop.bat` が「閉じて」と頼む相手です
            // ── Python の親は `py.exe` のこともあるので、親の番号では
            // なくこれを渡します(`bridge.py` の `exe_pid`)
            .env("XLCSV_EXE_PID", std::process::id().to_string())
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // コンソールの窓を出さない
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = command.spawn()?;
        let stdout = child.stdout.take().expect("stdout");
        let stderr = child.stderr.take().expect("stderr");
        *self.stdin.lock().unwrap() = child.stdin.take();
        *self.child.lock().unwrap() = Some(child);
        self.state.lock().unwrap().python = program.to_string();

        // 標準エラー: 末尾だけ覚えます(Python のログもここへ出ます)。
        // **UTF-8 でない行が来ても読み続けます** ── 読む側が居なくなると
        // 管が詰まり、Python がログを1行書くところで止まります
        let tail = self.stderr.clone();
        thread::Builder::new()
            .name("python-stderr".into())
            .spawn(move || keep_tail(stderr, &tail))?;

        let me = self.clone();
        thread::Builder::new()
            .name("python-stdout".into())
            .spawn(move || me.read_loop(stdout))?;
        Ok(())
    }

    fn read_loop(self: Arc<Self>, stdout: impl Read) {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let mut was_started = false;
        let mut said_quit = false;
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let head: Value = match serde_json::from_str(line.trim_end()) {
                Ok(found) => found,
                Err(_) => continue, // 形の崩れた行は飛ばす(本来は来ない)
            };
            let len = head.get("len").and_then(Value::as_u64).unwrap_or(0) as usize;
            let mut body = vec![0u8; len];
            if len > 0 && reader.read_exact(&mut body).is_err() {
                break;
            }
            if let Some(event) = head.get("event").and_then(Value::as_str) {
                was_started |= event == "started";
                said_quit |= event == "quit";
                self.on_event(event, &head);
                continue;
            }
            let id = head.get("id").and_then(Value::as_u64).unwrap_or(0);
            let reply = Reply {
                status: head.get("status").and_then(Value::as_u64).unwrap_or(500) as u16,
                headers: head
                    .get("headers")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|pair| {
                                let pair = pair.as_array()?;
                                Some((
                                    pair.first()?.as_str()?.to_string(),
                                    pair.get(1)?.as_str()?.to_string(),
                                ))
                            })
                            .collect()
                    })
                    .unwrap_or_default(),
                body,
            };
            if let Some(sender) = self.pending.lock().unwrap().remove(&id) {
                let _ = sender.send(reply);
            }
        }
        // 終わりました。待っている要求には答えられません
        self.pending.lock().unwrap().clear();
        self.set_phase(Phase::Ended, None);
        if was_started && !said_quit {
            // **落ちた。** 窓を読み直して理由の画面を出します
            if let Some(f) = self.on_lost.lock().unwrap().as_ref() {
                f();
            }
        }
    }

    fn on_event(&self, event: &str, head: &Value) {
        match event {
            "started" => self.set_phase(Phase::Started, None),
            "fatal" => {
                let text = |key: &str| {
                    head.get(key)
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string()
                };
                self.set_phase(
                    Phase::Ended,
                    Some(Failure {
                        message: text("message"),
                        hint: text("hint"),
                        log_dir: text("log_dir"),
                    }),
                );
            }
            "quit" => {
                if let Some(f) = self.on_quit.lock().unwrap().as_ref() {
                    f();
                }
            }
            _ => {}
        }
    }

    // --------------------------------------------------------------
    // 1件渡す
    // --------------------------------------------------------------
    pub fn call(
        &self,
        method: &str,
        path: &str,
        query: &str,
        headers: Vec<(String, String)>,
        body: &[u8],
    ) -> Result<Reply, String> {
        if self.phase() != Phase::Started {
            return Err("Python が動いていません".into());
        }
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let mut headers = headers;
        // **合言葉は外枠が付けます**(画面のHTMLに入っているものは使わない)
        headers.retain(|(k, _)| !k.eq_ignore_ascii_case("x-tool-token"));
        headers.push(("X-Tool-Token".into(), self.token.clone()));
        let head = json!({
            "id": id,
            "method": method,
            "path": path,
            "query": query,
            "headers": headers.iter().map(|(k, v)| vec![k.clone(), v.clone()]).collect::<Vec<_>>(),
            "len": body.len(),
        });
        let (sender, receiver) = mpsc::channel();
        self.pending.lock().unwrap().insert(id, sender);
        {
            let mut stdin = self.stdin.lock().unwrap();
            let Some(pipe) = stdin.as_mut() else {
                self.pending.lock().unwrap().remove(&id);
                return Err("Python への通り道が閉じています".into());
            };
            let mut frame = serde_json::to_vec(&head).map_err(|e| e.to_string())?;
            frame.push(b'\n');
            frame.extend_from_slice(body);
            if let Err(e) = pipe.write_all(&frame).and_then(|_| pipe.flush()) {
                self.pending.lock().unwrap().remove(&id);
                return Err(format!("Python へ渡せませんでした: {e}"));
            }
        }
        receiver.recv_timeout(REPLY_TIMEOUT).map_err(|_| {
            self.pending.lock().unwrap().remove(&id);
            "Python から応答がありません".to_string()
        })
    }

    /// 終える。**標準入力を閉じて** Python に終わってもらい、終わらなければ止めます。
    pub fn shutdown(&self, grace: Duration) {
        self.stdin.lock().unwrap().take(); // 閉じる = 「もう要求は来ない」
        let mut child = self.child.lock().unwrap();
        if let Some(child) = child.as_mut() {
            let deadline = Instant::now() + grace;
            while Instant::now() < deadline {
                if let Ok(Some(_)) = child.try_wait() {
                    return;
                }
                thread::sleep(Duration::from_millis(50));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// 試す Python の順番。`XLCSV_PYTHON` があればそれだけを使います。
///
/// **Python は同梱しません。** 各PCに入っているものを使います
/// (追加ライブラリは要りません ── 標準ライブラリだけで動きます)。
fn python_candidates() -> Vec<(String, Vec<String>)> {
    if let Ok(found) = std::env::var("XLCSV_PYTHON") {
        let found = found.trim().to_string();
        if !found.is_empty() {
            return vec![(found, vec![])];
        }
    }
    if cfg!(windows) {
        vec![
            ("python.exe".into(), vec![]),
            ("py.exe".into(), vec!["-3".into()]),
            ("python".into(), vec![]),
        ]
    } else {
        vec![("python3".into(), vec![]), ("python".into(), vec![])]
    }
}

/// 標準エラーの末尾だけ覚える。**UTF-8 でない行でも読み続けます。**
fn keep_tail(stream: impl Read, tail: &Mutex<std::collections::VecDeque<String>>) {
    let mut reader = BufReader::new(stream);
    let mut raw: Vec<u8> = Vec::new();
    loop {
        raw.clear();
        match reader.read_until(b'\n', &mut raw) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let line = String::from_utf8_lossy(&raw).trim_end().to_string();
        if line.is_empty() {
            continue;
        }
        let mut tail = tail.lock().unwrap();
        if tail.len() >= STDERR_LINES {
            tail.pop_front();
        }
        tail.push_back(line);
    }
}

/// 起動ごとの合言葉(`X-Tool-Token`)。
pub fn new_token() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{:x}{:x}{:x}", now, std::process::id(), &now.wrapping_mul(2654435761))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 合言葉は毎回ちがう() {
        assert_ne!(new_token(), new_token());
        assert!(new_token().len() >= 16);
    }

    #[test]
    fn pythonの候補は環境変数で決められる() {
        std::env::set_var("XLCSV_PYTHON", "/opt/python/bin/python3");
        let found = python_candidates();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].0, "/opt/python/bin/python3");
        std::env::remove_var("XLCSV_PYTHON");
        assert!(python_candidates().len() >= 2);
    }

    #[test]
    fn 標準エラーは末尾だけ覚える() {
        let tail = Mutex::new(std::collections::VecDeque::new());
        let mut text = String::new();
        for i in 0..(STDERR_LINES + 10) {
            text.push_str(&format!("行 {i}\n"));
        }
        // UTF-8 でない行が混じっても読み続ける
        let mut raw = text.into_bytes();
        raw.extend_from_slice(&[0x93, 0xfa, 0x95, 0xf1, b'\n']);
        keep_tail(&raw[..], &tail);
        let tail = tail.lock().unwrap();
        assert_eq!(tail.len(), STDERR_LINES);
        assert!(tail.back().is_some());
    }
}
