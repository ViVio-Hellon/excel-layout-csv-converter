//! 外枠が自分で出す画面。**Python がまだ居ない / 居なくなった**ときだけ使います。
//!
//! ふだんの画面(起動待機画面も含めて)は Python が返します ── 2枚持つと、
//! 直したほうと直していないほうが場面によって出ます。

use crate::bridge::Failure;

fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

const STYLE: &str = r#"
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f4f6;color:#16202b;
      font-family:system-ui,"Yu Gothic UI","Meiryo UI",sans-serif;line-height:1.75}
 .box{width:min(640px,calc(100vw - 48px));background:#fff;border:1px solid #d7dde4;border-radius:3px;
      padding:28px 32px;box-shadow:0 6px 20px rgba(22,32,43,.08)}
 h1{margin:0 0 12px;font-size:19px}
 h1.ng{color:#9b2c2c}
 .hint{margin-top:16px;padding:14px;background:#fdf3e2;border-left:4px solid #8a5300;white-space:pre-wrap}
 dt{color:#5b6b7c;font-size:13px;margin-top:14px}
 dd{margin:2px 0 0}
 code,pre{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:rgba(0,0,0,.05);
          padding:2px 5px;border-radius:2px;word-break:break-all}
 pre{padding:10px;max-height:16em;overflow:auto;white-space:pre-wrap}
 .spin{display:inline-block;width:14px;height:14px;margin-right:8px;border:2px solid #1d5b8f;
       border-right-color:transparent;border-radius:50%;animation:t .8s linear infinite}
 @keyframes t{to{transform:rotate(360deg)}}
"#;

/// Python が「受け付け始めた」と言う前の、ほんの一瞬に出す画面。
///
/// 1秒ごとに読み直し、Python の**待機画面**(段と進み具合が出る)へ移ります。
pub fn starting() -> String {
    format!(
        "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">\
         <title>起動しています</title><style>{STYLE}</style>\
         <meta http-equiv=\"refresh\" content=\"1\"></head><body>\
         <div class=\"box\"><h1><span class=\"spin\"></span>起動しています</h1>\
         <p>Python を起こしています。しばらくお待ちください。</p></div></body></html>"
    )
}

/// Python が起動できない / 落ちたときの画面。**次に何をすればよいかまで出します。**
pub fn failure(found: Option<&Failure>, python: &str, stderr: &[String], log_hint: &str) -> String {
    let empty = Failure::default();
    let found = found.unwrap_or(&empty);
    let message = if found.message.is_empty() {
        "Python の処理が終了しました".to_string()
    } else {
        found.message.clone()
    };
    let hint = if found.hint.is_empty() {
        "このツールを開き直してください。続くときは、下のログを見てください。".to_string()
    } else {
        found.hint.clone()
    };
    let log_dir = if found.log_dir.is_empty() {
        log_hint.to_string()
    } else {
        found.log_dir.clone()
    };
    let tail = if stderr.is_empty() {
        String::new()
    } else {
        format!(
            "<dt>Python が言ったこと(最後の{}行)</dt><dd><pre>{}</pre></dd>",
            stderr.len(),
            escape(&stderr.join("\n"))
        )
    };
    format!(
        "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">\
         <title>起動できません</title><style>{STYLE}</style></head><body>\
         <div class=\"box\"><h1 class=\"ng\">{}</h1>\
         <div class=\"hint\">{}</div>\
         <dl><dt>使おうとした Python</dt><dd><code>{}</code></dd>\
         <dt>ログの置き場所</dt><dd><code>{}</code></dd>{}</dl>\
         </div></body></html>",
        escape(&message),
        escape(&hint),
        escape(if python.is_empty() { "(見つかりません)" } else { python }),
        escape(&log_dir),
        tail
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 起動中の画面は読み直す() {
        let html = starting();
        assert!(html.contains("http-equiv=\"refresh\""));
        assert!(html.contains("起動しています"));
    }

    #[test]
    fn 失敗の画面は理由と次の手を出す() {
        let found = Failure {
            message: "ブラウザ版が起動しています".into(),
            hint: "先に stop.bat を実行してください".into(),
            log_dir: "C:\\\\logs".into(),
        };
        let html = failure(Some(&found), "python.exe", &["あ".into()], "(既定)");
        assert!(html.contains("ブラウザ版が起動しています"));
        assert!(html.contains("stop.bat"));
        assert!(html.contains("python.exe"));
        assert!(html.contains("C:\\\\logs"));
    }

    #[test]
    fn 理由が無くても画面は出る() {
        let html = failure(None, "", &[], "(既定)");
        assert!(html.contains("Python の処理が終了しました"));
        assert!(html.contains("開き直して"));
    }

    #[test]
    fn 危ない字はそのまま出さない() {
        let found = Failure { message: "<script>x</script>".into(), ..Failure::default() };
        let html = failure(Some(&found), "", &[], "");
        assert!(!html.contains("<script>x"));
        assert!(html.contains("&lt;script&gt;"));
    }
}
