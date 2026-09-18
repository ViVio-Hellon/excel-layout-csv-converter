/* 画面の動き
 *
 * 【この画面は1枚だけ】
 * 2枚開けると同じ作業状態を奪い合うので、開くこと自体を断ります。判定は
 * 2段:
 *
 *   1. 同じブラウザの中   BroadcastChannel で「もう1枚いますか」と聞く
 *                         ── 複製タブは sessionStorage ごと複製されるので、
 *                            サーバ側の識別子だけでは見分けられません
 *   2. サーバ             /api/screen/claim ── 別のブラウザや、URLを直接
 *                         打った場合はこちらで断ります
 *
 * 断られた画面は覆いを出し、以後どのAPIも呼びません(合図も持ちません)。
 *
 * 【ダウンロードはしません】
 * 出したCSVは、設定部で指定したフォルダへ**この端末のPythonが**書きます。
 * ブラウザのダウンロードフォルダに散らばると、どれが最新か分からなく
 * なるためです。放り込まれたブックだけは、中身を送って受け取ります
 * (ブラウザは場所を教えないため)。
 */
(function () {
  "use strict";

  var BOOT = window.BOOT || {};
  var CHANNEL_NAME = "xlcsv-screen";
  var token = "";
  var active = false;          // この画面が使ってよい状態か
  var beatTimer = null;
  var pollTimer = null;
  var missedBeats = 0;
  var jobRunning = false;
  var lastJobId = "";

  var $ = function (id) { return document.getElementById(id); };
  var channel = ("BroadcastChannel" in window)
    ? new BroadcastChannel(CHANNEL_NAME) : null;
  var loadId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  // ----------------------------------------------------------------
  // 通信
  // ----------------------------------------------------------------
  function api(path, options) {
    options = options || {};
    var headers = options.headers || {};
    if (token) { headers["X-Screen-Token"] = token; }
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.json);
      options.method = options.method || "POST";
    }
    return fetch(path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body,
      cache: "no-store",
      keepalive: !!options.keepalive
    }).then(function (response) {
      return response.json().catch(function () { return {}; })
        .then(function (data) {
          if (data && data.screen_lost) { lost(); }
          return { status: response.status, ok: response.ok, data: data };
        });
    });
  }

  // ----------------------------------------------------------------
  // 断る・切れる
  // ----------------------------------------------------------------
  function gate(title, detail, note, options) {
    options = options || {};
    active = false;
    token = "";
    clearInterval(beatTimer); clearInterval(pollTimer);
    $("gate-title").textContent = title;
    $("gate-detail").textContent = detail;
    $("gate-note").textContent = note || "";
    $("gate-mark").textContent = options.mark || "!";
    $("gate-retry").hidden = !!options.hideRetry;
    $("gate").hidden = false;
    $("main").hidden = true;
    document.body.classList.remove("booting");
  }

  function lost() {
    if (!active) { return; }
    gate("この画面は使えなくなりました",
         "別の画面で開かれたか、しばらく応答がありませんでした。" +
         "取り違えを防ぐため、この画面の操作は受け付けません。",
         "変換が動いていた場合は、そのまま続いています。");
  }

  $("gate-retry").addEventListener("click", function () { location.reload(); });
  $("gate-close").addEventListener("click", function () {
    window.close();
    $("gate-note").textContent =
      "閉じられませんでした。このタブは手で閉じてください。";
  });

  // ----------------------------------------------------------------
  // 1枚だけにする
  // ----------------------------------------------------------------
  function askOthers() {
    // 同じブラウザに、もう1枚この画面が開いていないか聞く
    return new Promise(function (resolve) {
      if (!channel) { return resolve(false); }
      var answered = false;
      channel.onmessage = function (event) {
        var message = event.data || {};
        if (message.type === "ping" && active) {
          channel.postMessage({ type: "pong", to: message.from });
        } else if (message.type === "pong" && message.to === loadId) {
          answered = true;
        }
      };
      channel.postMessage({ type: "ping", from: loadId });
      setTimeout(function () { resolve(answered); }, 300);
    });
  }

  function claim() {
    var tabId = "";
    try {
      tabId = sessionStorage.getItem("xlcsv-tab") || "";
      if (!tabId) {
        tabId = Math.random().toString(36).slice(2) + Date.now().toString(36);
        sessionStorage.setItem("xlcsv-tab", tabId);
      }
    } catch (e) {          // 保存が禁止されている端末でも開けるように
      tabId = loadId;
    }
    return api("/api/screen/claim",
               { json: { tab_id: tabId, load_id: loadId } });
  }

  function boot() {
    askOthers().then(function (someoneElse) {
      if (someoneElse) {
        return gate("この画面は開けません",
          "同じアプリの画面が、すでにこのブラウザで開かれています。" +
          "2枚開くと、どちらの操作が効いているのか分からなくなるため、" +
          "開くことをお断りしています。",
          "先に開いている画面をお使いください。", { hideRetry: false });
      }
      claim().then(function (result) {
        if (!result.ok) {
          return gate(result.data.reason || "この画面は開けません",
                      result.data.detail || "",
                      "先に開いている画面を閉じると、" +
                      (result.data.retry_after || 12) +
                      "秒ほどで開けるようになります。");
        }
        token = result.data.token;
        active = true;
        $("gate").hidden = true;
        $("main").hidden = false;
        document.body.classList.remove("booting");
        start();
      }).catch(function () {
        gate("バックエンドへ繋がりません",
             "アプリの本体が動いていないようです。",
             "start.bat(診断用)で起動すると、コンソールに理由が出ます。");
      });
    });
  }

  window.addEventListener("pagehide", function () {
    if (!active) { return; }
    // **閉じたぶんは空けます。** そうしないと、次に開くまで待たされます
    api("/api/screen/release", { json: {}, keepalive: true });
    if (channel) { channel.postMessage({ type: "bye", from: loadId }); }
  });

  // ----------------------------------------------------------------
  // 生存確認(基盤仕様書 2.9)
  // ----------------------------------------------------------------
  function beat() {
    api("/api/screen/beat", { json: {} }).then(function (result) {
      if (!result.ok) { return lost(); }
      missedBeats = 0;
      setConnection(true);
      if (!pollTimer && result.data.job) { render(result.data.job); }
    }).catch(function () {
      missedBeats += 1;
      setConnection(false);
      if (missedBeats >= 3) {
        gate("バックエンドとの接続が切れました",
             "アプリの本体が終了したか、応答しなくなりました。",
             "変換中だった場合は、ログで最後の状態を確認できます。");
      }
    });
  }

  function setConnection(ok) {
    $("conn").className = ok ? "conn" : "conn lost";
    $("conn-text").textContent = ok ? "接続中" : "応答がありません";
  }

  // ----------------------------------------------------------------
  // 設定部
  // ----------------------------------------------------------------
  var FIELDS = ["input_dir", "output_dir", "line", "ref_dir"];

  function showSettings(data) {
    FIELDS.forEach(function (key) {
      if (document.activeElement !== $(key)) {
        $(key).value = (data.settings && data.settings[key]) || "";
      }
    });
    (data.paths || []).forEach(function (item) {
      var hint = $("hint-" + item.key);
      if (!hint) { return; }
      if (!item.text) { hint.textContent = ""; hint.className = "hint"; return; }
      hint.textContent = item.resolved + (item.note ? " ── " + item.note : "");
      hint.className = "hint " + (item.exists ? "ok" : (item.note ? "bad" : ""));
    });
  }

  function loadSettings() {
    api("/api/settings").then(function (r) { if (r.ok) { showSettings(r.data); } });
  }

  $("save").addEventListener("click", function () {
    var payload = {};
    FIELDS.forEach(function (key) { payload[key] = $(key).value; });
    api("/api/settings", { json: payload }).then(function (r) {
      var message = $("settings-msg");
      if (!r.ok) {
        message.textContent = (r.data.reason || "保存できません") +
          (r.data.detail ? " ── " + r.data.detail : "");
        message.className = "msg bad";
        return;
      }
      showSettings(r.data);
      message.textContent = "保存しました";
      message.className = "msg ok";
      setTimeout(function () { message.textContent = ""; }, 4000);
    });
  });

  $("open-out").addEventListener("click", function () { openWhat("out"); });
  $("open-logs").addEventListener("click", function () { openWhat("logs"); });

  function openWhat(what) {
    api("/api/open", { json: { what: what } }).then(function (r) {
      if (!r.ok) {
        $("settings-msg").textContent =
          (r.data.reason || "開けません") + " ── " + (r.data.detail || "");
        $("settings-msg").className = "msg bad";
      }
    });
  }

  // ----------------------------------------------------------------
  // 入力(ドラッグ投入)
  // ----------------------------------------------------------------
  var drop = $("drop");
  var picker = $("picker");

  function sourceIsFolder() {
    return document.querySelector("input[name=source]:checked").value === "folder";
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("input[name=source]"), function (radio) {
      radio.addEventListener("change", function () {
        drop.classList.toggle("off", sourceIsFolder());
        $("folder-line").hidden = !sourceIsFolder();
      });
    });

  ["dragenter", "dragover"].forEach(function (name) {
    drop.addEventListener(name, function (event) {
      event.preventDefault();
      if (!jobRunning) { drop.classList.add("over"); }
    });
  });
  ["dragleave", "drop"].forEach(function (name) {
    drop.addEventListener(name, function () { drop.classList.remove("over"); });
  });
  // 画面の外で落として、ブラウザがブックを開いてしまうのを防ぐ
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); });

  drop.addEventListener("drop", function (event) {
    event.preventDefault();
    if (jobRunning) { return; }
    collect(event.dataTransfer).then(receive);
  });
  drop.addEventListener("click", function () { if (!jobRunning) { picker.click(); } });
  drop.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") { picker.click(); }
  });
  picker.addEventListener("change", function () {
    receive(filterBooks(Array.prototype.slice.call(picker.files)));
    picker.value = "";
  });

  function filterBooks(files) {
    return files.filter(function (file) {
      var name = (file.name || "").toLowerCase();
      return (/\.(xlsx|xlsm)$/.test(name)) && name.indexOf("~$") !== 0;
    });
  }

  function collect(transfer) {
    // フォルダごと放り込まれたら、中のブックを拾います
    var items = transfer.items;
    if (!items || !items.length || !items[0].webkitGetAsEntry) {
      return Promise.resolve(filterBooks(
        Array.prototype.slice.call(transfer.files || [])));
    }
    var entries = [];
    for (var i = 0; i < items.length; i += 1) {
      var entry = items[i].webkitGetAsEntry();
      if (entry) { entries.push(entry); }
    }
    return Promise.all(entries.map(walk)).then(function (lists) {
      return filterBooks([].concat.apply([], lists));
    });
  }

  function walk(entry) {
    if (entry.isFile) {
      return new Promise(function (resolve) {
        entry.file(function (file) { resolve([file]); },
                   function () { resolve([]); });
      });
    }
    if (!entry.isDirectory) { return Promise.resolve([]); }
    var reader = entry.createReader();
    return new Promise(function (resolve) {
      var found = [];
      (function readMore() {
        reader.readEntries(function (batch) {
          if (!batch.length) {
            return Promise.all(found.map(walk)).then(function (lists) {
              resolve([].concat.apply([], lists));
            });
          }
          found = found.concat(Array.prototype.slice.call(batch));
          readMore();
        }, function () { resolve([]); });
      })();
    });
  }

  function receive(files) {
    if (!files.length) {
      return setIntakeMessage("読めるブックがありませんでした(.xlsx / .xlsm)", true);
    }
    var limit = BOOT.max_files || 100;
    var extra = 0;
    if (files.length > limit) {
      extra = files.length - limit;
      files = files.slice(0, limit);
    }
    files.sort(function (a, b) { return a.name.localeCompare(b.name, "ja"); });

    setBusy(true);
    showProgress("受け取り", 0, files.length, "");
    api("/api/input/begin", { json: {} }).then(function () {
      var index = 0;
      var refused = [];
      function next() {
        if (index >= files.length) {
          hideProgress();
          setBusy(false);
          refreshIntake();
          var message = files.length + " 本を受け取りました";
          if (extra) {
            message += " ── " + limit + " 本を超えたぶん(" + extra +
                       " 本)は受け取っていません";
          }
          if (refused.length) { message += " ／ " + refused.join(" ／ "); }
          return setIntakeMessage(message, extra > 0 || refused.length > 0);
        }
        var file = files[index];
        index += 1;
        showProgress("受け取り", index, files.length, file.name);
        api("/api/input/add", {
          method: "POST",
          headers: { "X-Filename": encodeURIComponent(file.name) },
          body: file
        }).then(function (r) {
          if (r.data && r.data.ok === false && r.data.message) {
            refused.push(r.data.message);
          }
          next();
        }).catch(function () { refused.push(file.name + ": 送れませんでした"); next(); });
      }
      next();
    });
  }

  function setIntakeMessage(text, warn) {
    $("intake-msg").textContent = text;
    $("intake-msg").className = "msg" + (warn ? " bad" : " ok");
  }

  function refreshIntake() {
    api("/api/input").then(function (r) {
      if (!r.ok) { return; }
      var limit = r.data.limit || BOOT.max_files;
      $("intake-count").textContent = r.data.count + " / " + limit + " 本";
      $("intake-count").className = "count" + (r.data.count >= limit ? " full" : "");
      $("intake-list").innerHTML = "";
      (r.data.names || []).forEach(function (name) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = name;
        $("intake-list").appendChild(chip);
      });
    });
  }

  $("intake-clear").addEventListener("click", function () {
    api("/api/input/clear", { json: {} }).then(function () {
      refreshIntake();
      setIntakeMessage("受け取ったぶんを消しました", false);
    });
  });

  $("scan").addEventListener("click", function () {
    api("/api/input/scan", { json: { folder: $("input_dir").value } })
      .then(function (r) {
        $("scan-msg").textContent = r.data.message || "";
        $("scan-msg").className = "msg" + (r.data.ok ? " ok" : " bad");
      });
  });

  // ----------------------------------------------------------------
  // 変換
  // ----------------------------------------------------------------
  $("start").addEventListener("click", function () {
    var payload = {
      source: sourceIsFolder() ? "folder" : "intake",
      dry_run: $("dry_run").checked
    };
    notice("", null);
    api("/api/job/start", { json: payload }).then(function (r) {
      if (!r.ok) {
        return notice((r.data.reason || "始められません") + " ── " +
                      (r.data.detail || ""), "bad");
      }
      render(r.data.job);
      watch();
    });
  });

  $("cancel").addEventListener("click", function () {
    if (!window.confirm("変換を中止しますか?\n\n" +
        "読み取りの途中なら止まります。CSVは書きません。")) { return; }
    api("/api/job/cancel", { json: {} }).then(function (r) {
      if (r.data && r.data.job) { render(r.data.job); }
    });
  });

  function watch() {
    clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      api("/api/job").then(function (r) {
        if (r.ok) { render(r.data.job); }
      });
    }, BOOT.job_poll_ms || 400);
  }

  function showProgress(phase, done, total, current) {
    $("progress").hidden = false;
    $("progress-phase").textContent = phase;
    $("progress-count").textContent = done + " / " + total + " 本";
    $("progress-fill").style.width =
      (total ? Math.round(done * 100 / total) : 0) + "%";
    $("progress-current").textContent = current || "";
  }

  function hideProgress() { $("progress").hidden = true; }

  function setBusy(busy) {
    jobRunning = busy;
    $("start").disabled = busy;
    $("cancel").hidden = !busy;
    $("save").disabled = busy;
    $("intake-clear").disabled = busy;
    drop.classList.toggle("off", busy || sourceIsFolder());
    FIELDS.forEach(function (key) { $(key).disabled = busy; });
  }

  function notice(text, kind) {
    var box = $("job-msg");
    box.hidden = !text;
    box.textContent = text;
    box.className = "notice" + (kind ? " " + kind : "");
  }

  function render(job) {
    if (!job || !job.id) { return; }
    setBusy(job.running);
    if (job.running) {
      var time = job.elapsed + " 秒経過";
      if (job.remaining !== null && job.remaining !== undefined) {
        time += " ／ あと " + job.remaining + " 秒くらい";
      }
      showProgress(job.phase, job.done, job.total, job.current);
      $("progress-time").textContent = time;
      notice(job.message || "", null);
    } else {
      clearInterval(pollTimer); pollTimer = null;
      showProgress(job.phase || "終わりました", job.done, job.total, "");
      $("progress-time").textContent = job.elapsed + " 秒かかりました";
      var kind = job.counts.bad ? "bad" : (job.counts.suspect ? "warn" : null);
      notice((job.state + " ── " + (job.message || "")) +
             (job.error ? " / " + job.error : "") +
             (job.dry_run || !job.out_dir ? "" : "  出力先: " + job.out_dir),
             kind);
      if (job.id !== lastJobId) {
        lastJobId = job.id;
        refreshIntake();
        loadSettings();        // 出力先は書いたあとに出来ているので見直す
      }
    }
    if (job.master_notice && !job.master_ok) {
      $("scan-msg").textContent = job.master_notice;
      $("scan-msg").className = "msg bad";
    }
    renderResult(job);
  }

  function renderResult(job) {
    if (!job.files.length) { $("result-card").hidden = true; return; }
    $("result-card").hidden = false;

    var counts = job.counts;
    $("tiles").innerHTML = "";
    [["読めた", counts.read_ok + " / " + counts.files, ""],
     ["数が合った", counts.verified, ""],
     ["数が合わない", counts.suspect, counts.suspect ? "warn" : ""],
     ["読めなかった", counts.bad, counts.bad ? "bad" : ""],
     [job.dry_run ? "書いたCSV(確認のみ)" : "書いたCSV", job.written, ""]
    ].forEach(function (item) {
      var tile = document.createElement("div");
      tile.className = "tile " + item[2];
      tile.innerHTML = "<b></b><span></span>";
      tile.querySelector("b").textContent = item[1];
      tile.querySelector("span").textContent = item[0];
      $("tiles").appendChild(tile);
    });

    var body = $("result").querySelector("tbody");
    body.innerHTML = "";
    job.files.forEach(function (file) {
      var row = document.createElement("tr");
      row.className = !file.ok ? "ng" : (file.verify_ok ? "ok" : "suspect");
      var detail = file.ok
        ? (file.marks && file.marks.length ? file.marks.join(" ／ ") : file.verify)
        : file.error;
      [[file.mark, "mark"], [file.name, ""], [file.report_date, ""],
       [(file.line_text ? file.line_text + "→" : "") + file.line, ""],
       [file.pages || "", "num"], [file.rows || "", "num"],
       [detail || "", "detail"]
      ].forEach(function (cell) {
        var td = document.createElement("td");
        td.className = cell[1];
        td.textContent = cell[0];
        row.appendChild(td);
      });
      body.appendChild(row);
    });
  }

  // ----------------------------------------------------------------
  // 終了(基盤仕様書 2.8)
  // ----------------------------------------------------------------
  $("quit").addEventListener("click", function () { quit(false); });

  function quit(force) {
    api("/api/shutdown", { json: { force: force } }).then(function (r) {
      if (r.status === 409 && r.data.running) {
        if (window.confirm(r.data.reason + "\n\n" + r.data.detail)) {
          return quit(true);
        }
        return;
      }
      if (!r.ok) {
        return notice((r.data.reason || "終了できません") + " ── " +
                      (r.data.detail || ""), "bad");
      }
      gate("終了しました", "バックエンドを終了しました。" +
           "このタブは閉じてかまいません。",
           "もう一度使うときは Start.vbs から起動してください。",
           { mark: "✓", hideRetry: true });
    }).catch(function () {
      gate("終了しました", "バックエンドを終了しました。" +
           "このタブは閉じてかまいません。", "", { mark: "✓", hideRetry: true });
    });
  }

  // ----------------------------------------------------------------
  // 診断(基盤仕様書 2.6)
  // ----------------------------------------------------------------
  $("diag-toggle").addEventListener("click", function () {
    var box = $("diag");
    if (!box.hidden) { box.hidden = true; return; }
    api("/api/diagnostics").then(function (r) {
      box.textContent = [r.data.text,
                         "Python        : " + r.data.python + " (" + r.data.executable + ")",
                         "PID           : " + r.data.pid,
                         "ログ          : " + (r.data.logs || {}).folder,
                         "受け取り置き場: " + r.data.intake,
                         r.data.config_error, r.data.version_problem]
        .filter(Boolean).join("\n");
      box.hidden = false;
    });
  });

  // ----------------------------------------------------------------
  // はじめ
  // ----------------------------------------------------------------
  function start() {
    $("limit-text").textContent = BOOT.max_files;
    $("intake-count").textContent = "0 / " + BOOT.max_files + " 本";
    loadSettings();
    refreshIntake();
    beat();
    beatTimer = setInterval(beat, BOOT.heartbeat_ms || 3000);
    api("/api/job").then(function (r) {
      // 閉じているあいだに走っていた変換があれば、そのまま引き継ぎます
      if (r.ok && r.data.job && r.data.job.id) {
        render(r.data.job);
        if (r.data.job.running) { watch(); }
      }
    });
  }

  boot();
})();
