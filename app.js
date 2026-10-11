"use strict";
// Alfred's window. Talks only to its own local server (server.py): JSON over /api and the event
// feed at /api/events, each call carrying this launch's key. All text from Alfred, Telegram or files
// is shown with textContent, never as HTML. Voice mode also talks to OpenAI's live voice model over
// WebRTC; the server starts that session (app/voice.py), so the OpenAI key never reaches the page.

(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };

  const STATES = {
    off: "Off", needs_consent: "Needs your OK", starting: "Starting", ready: "Ready", busy: "Working",
    stopping: "Stopping", crashed: "Crashed", offline: "Not connected",
  };
  const TELEGRAM = {
    off: "off", not_configured: "not set up", starting: "starting", discovery: "waiting for its owner",
    running: "on", error: "error",
  };
  const PLACES = {
    desktop: "this window", telegram: "Telegram", remote: "another of your PCs", voice: "voice",
    console: "the console", hotkey: "the Stop hotkey", tray: "the tray icon", timeout: "nobody in time",
    stop: "Stop", exit: "Alfred closing", shutdown: "Alfred closing", restart: "a restart",
  };
  const ASKED = {
    desktop: "Asked by a command from this window.",
    telegram: "Asked by a command from Telegram.",
    remote: "Another of your PCs wants to do this on this PC.",
    voice: "Asked in voice mode. Answer here: nobody can approve by voice.",
  };
  const YOU = { desktop: "You", voice: "You, by voice" };
  const ALFRED = { desktop: "Alfred", voice: "Alfred, by voice" };
  const SECRETS = [
    ["ANTHROPIC_API_KEY", "Anthropic API key", "Needed for anything beyond simple built-in commands."],
    ["OPENAI_API_KEY", "OpenAI API key", "Optional: voice mode here, and voice notes and spoken replies in Telegram."],
    ["TELEGRAM_BOT_TOKEN", "Telegram bot token", "From @BotFather (/newbot) in Telegram."],
    ["SPECTRUM_PROJECT_SECRET", "Photon Spectrum secret", "Connects your Telegram bot to Alfred."],
  ];
  const place = (name) => PLACES[name] || name || "Alfred";

  const log = $("log");
  const input = $("input");
  const stopButton = $("stop");
  let lastSeq = null;
  let feed = null;
  let retryMs = 1000;
  let retryTimer = null;
  let signedOut = false;
  let paused = false;
  let settings = null;
  let telegramState = "off";
  let engineError = "";
  const replied = new Set();
  const openedAt = Date.now();
  let ownerHinted = false;

  // --- server -----------------------------------------------------------------
  // The key comes in the page /auth served, then lives in sessionStorage, which only this origin
  // (127.0.0.1 at Alfred's port) can read. Not a cookie: browsers send those to every port.
  const key = (() => {
    const meta = document.querySelector('meta[name="alfred-key"]');
    let value = meta ? meta.content : "";
    if (meta) meta.remove();
    try {
      if (value) sessionStorage.setItem("alfred-key", value);
      else value = sessionStorage.getItem("alfred-key") || "";
    } catch { /* storage blocked: this page works until it reloads */ }
    /* web demo: there is no sign-in link in the address to hide */
    return value;
  })();

  async function api(method, path, body) {
    const options = { method, headers: { "X-Alfred": key } };
    if (method !== "GET") {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body || {});
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("Alfred isn't answering. Is it still running?");
    }
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (response.status === 401) onSignedOut();
    if (!response.ok) throw new Error(data.error || `Alfred answered ${response.status}.`);
    return data;
  }

  // The event feed is read with fetch, not EventSource, so it can send the key and notice a 401.
  async function connect() {
    if (signedOut || paused) return;
    clearTimeout(retryTimer);
    if (feed) feed.abort();
    const mine = feed = new AbortController();
    let opened = false;
    try {
      const response = await fetch(lastSeq === null ? "/api/events" : `/api/events?after=${lastSeq}`,
        { headers: { "X-Alfred": key }, signal: mine.signal });
      if (response.status === 401) return onSignedOut();
      if (!response.ok || !response.body) throw new Error(`Alfred answered ${response.status}.`);
      opened = true;
      retryMs = 1000;
      banner("conn", null);
      refreshStatus();
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let end;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          for (const row of block.split("\n")) {
            if (!row.startsWith("data:")) continue;  // ids repeat each event's seq; ":" lines keep it alive
            let event;
            try { event = JSON.parse(row.slice(5)); } catch { continue; }
            try { handle(event); } catch (e) { console.error(e); }
          }
        }
      }
    } catch { /* dropped, stopped on purpose, or Alfred is gone */ }
    if (feed !== mine) return;
    feed = null;
    setPill("offline");
    banner("conn", opened ? "Lost the connection to Alfred. Trying again…" : "Alfred isn't answering. Trying again…");
    retryTimer = setTimeout(async () => {
      await refreshStatus();
      connect();
    }, retryMs);
    retryMs = Math.min(retryMs * 2, 15000);
  }

  function disconnect() {
    clearTimeout(retryTimer);
    if (feed) feed.abort();
    feed = null;
  }

  // Browsers allow about six connections to one server, so a page nobody is looking at lets go of
  // its feed. When it is shown again it catches up from lastSeq. Not during voice mode: Alfred's
  // window is often behind the apps it opens then, and approvals and Stop must still reach the call.
  let hideTimer = null;
  function resume() {
    clearTimeout(hideTimer);
    if (!paused || document.hidden) return;
    paused = false;
    connect();
  }
  function onVisibility() {
    clearTimeout(hideTimer);
    if (!document.hidden) resume();
    else hideTimer = setTimeout(() => {
      if (live) return;
      paused = true;
      disconnect();
    }, 10000);
  }
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pagehide", () => { endVoice(); paused = true; disconnect(); });
  window.addEventListener("pageshow", resume);

  function onSignedOut() {
    if (signedOut) return;
    signedOut = true;
    endVoice("Voice mode is off.");
    disconnect();
    setPill("offline");
    banner("conn", "This window isn't signed in to Alfred any more (Alfred may have restarted). " +
      "Open Alfred again from its tray icon or the Start menu.", "error");
  }

  async function refreshStatus() {
    let status;
    try { status = await api("GET", "/api/status"); } catch { return null; }
    engineError = status.last_error || "";
    setPill(status.engine);
    setTelegram(status.telegram, status.telegram_error);
    const hotkey = status.hotkey;
    $("hotkey-hint").textContent = hotkey && hotkey.ok ? ` · ${hotkey.keys} stops Alfred from any app` : "";
    banner("hotkey", hotkey && !hotkey.ok ? hotkey.error : null);
    $("quit").hidden = !status.quit;
    $("version").textContent = `Alfred ${status.version}.`;
    document.forms["settings-form"].elements.JARVIS_NAME.placeholder = status.pc_name || "";
    for (const pending of status.approvals || []) {
      if (!approvals.has(pending.token)) addApproval(pending);
    }
    refreshVoice();
    return status;
  }

  // --- events -----------------------------------------------------------------
  function handle(event) {
    if (typeof event.seq === "number") {
      if (lastSeq !== null && event.seq <= lastSeq) return;
      lastSeq = event.seq;
    }
    switch (event.type) {
      case "command":
        if (event.source === "desktop") following = true;  // sent from here: show it and what follows
        say("you", event.text, YOU[event.source] || `You, from ${place(event.source)}`);
        break;
      case "reply":
        replied.add(event.id);
        say("alfred", event.text, ALFRED[event.source || "desktop"] || `Alfred, to ${place(event.source)}`);
        break;
      case "status":
        line(event.text, "step");
        break;
      case "announce":
        say("announce", event.text, "Alfred");
        break;
      case "notice":
        line(event.text);
        break;
      case "stop":
        line(`Stopped from ${place(event.source)}. Alfred let go of the mouse and keyboard and is starting again.`, "stopped");
        endVoice("Voice mode is off.");
        break;
      case "voice_off":  // the window was closed to the tray
        endVoice("Voice mode is off: Alfred's window was closed.");
        break;
      case "done":
        if (!event.ok && !event.stopped && !replied.has(event.id)) line("That command didn't finish.", "error");
        replied.delete(event.id);
        break;
      case "crash":
        line(event.restart_in === null || event.restart_in === undefined
          ? `${event.error} Alfred stopped trying to restart.` : `${event.error} Restarting in ${event.restart_in} s.`, "error");
        break;
      case "ready":
        if (event.after_stop) line("Ready again.");
        else if (event.has_llm === false) line("Alfred is ready. Without an Anthropic API key it only understands simple commands; add one in Settings.");
        break;
      case "state":
        setPill(event.state);
        if (event.state === "crashed") refreshStatus();  // for why: status.last_error
        break;
      case "telegram":
        setTelegram(event.state, event.error);
        if (event.error) line(event.error, "error");
        break;
      case "telegram_user":  // messaging the bot never makes anyone its owner; the setup's code does
        if (!ownerHinted && event.ts * 1000 > openedAt) {  // once, and not for events from before this page
          ownerHinted = true;
          line("Someone messaged your Telegram bot, which has no owner yet, so Alfred ignored it. To link your own "
            + "Telegram account, open Settings → Setup → Telegram.");
        }
        break;
      case "approval":
        line(`Alfred asked: ${event.reason}`);
        addApproval(event);
        if (event.source === "voice") voiceApproval(event);
        break;
      case "approval_closed":
        line(`${event.approved ? "Allowed" : "Not allowed"} (answered by ${place(event.by)}).`);
        closeApproval(event.token);
        break;
      default:
        break;
    }
  }

  // The conversation follows new entries while it is scrolled to the end. That is noted when the reader
  // scrolls, not measured when an entry arrives: then the log may be hidden (another page is open) or
  // just made shorter, and would measure as scrolled up.
  let following = true;
  log.addEventListener("scroll", () => {
    if (log.clientHeight) following = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  });
  function toEnd() {
    if (following) log.scrollTop = log.scrollHeight;
  }
  if (window.ResizeObserver) new ResizeObserver(toEnd).observe(log);

  function append(node) {
    log.append(node);
    $("empty").hidden = true;
    toEnd();
    return node;
  }

  function say(kind, text, who) {
    const item = el("li", `entry ${kind}`);
    item.append(el("span", "meta", who), el("div", "text", text));
    return append(item);
  }

  function line(text, kind) {
    const item = el("li", `entry ${kind === "step" ? "step" : "line"} ${kind || ""}`);
    item.append(el("div", "text", text));
    return append(item);
  }

  function setPill(state) {
    const pill = $("pill");
    pill.dataset.state = state;
    pill.textContent = STATES[state] || state;
    pill.title = state === "crashed" || state === "needs_consent" ? engineError : "";
    if (state === "needs_consent") showConsent();
    else if (state !== "offline" && state !== "off") $("consent").hidden = true;
    banner("crash", state === "crashed" ? engineError || "Alfred's engine stopped." : null,
      "error", state === "crashed" ? ["Restart", restartEngine] : null);
  }

  function setTelegram(state, error) {
    telegramState = state || "off";
    const chip = $("telegram");
    chip.dataset.state = telegramState;
    chip.textContent = `Telegram: ${TELEGRAM[telegramState] || telegramState}`;
    chip.title = error || "Telegram settings";
    const on = ["running", "starting", "discovery"].includes(telegramState);
    $("telegram-toggle").textContent = on ? "Turn off" : "Turn on";
    $("telegram-state").textContent = error ? `Telegram: ${error}` : (telegramState === "not_configured"
      ? "Telegram isn't set up: save the bot token and Spectrum secret above."
      : `Telegram is ${TELEGRAM[telegramState] || telegramState}.`);
  }

  const banners = new Map();
  function banner(key, text, kind, action) {
    if (text) banners.set(key, { text, kind, action }); else banners.delete(key);
    const box = $("banners");
    box.textContent = "";
    for (const item of banners.values()) {
      const div = el("div", `banner ${item.kind || ""}`);
      div.append(el("span", null, item.text));
      if (item.action) {
        const button = el("button", null, item.action[0]);
        button.type = "button";
        button.onclick = item.action[1];
        div.append(button);
      }
      box.append(div);
    }
  }

  // --- commands ---------------------------------------------------------------
  async function send(event) {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    const button = $("send");
    button.disabled = true;
    try {
      await api("POST", "/api/command", { text });
      input.value = "";
      autosize();
    } catch (e) {
      line(e.message, "error");
    } finally {
      button.disabled = false;
      input.focus();
    }
  }

  async function stopEverything() {
    endVoice("Voice mode is off.");
    stopButton.classList.remove("pressed");
    void stopButton.offsetWidth;
    stopButton.classList.add("pressed");
    try {
      const result = await api("POST", "/api/stop");
      if (!result.stopped) line("Nothing to stop: Alfred's engine isn't running.");
    } catch (e) {
      line(e.message, "error");
    }
  }

  async function restartEngine() {
    try {
      await api("POST", "/api/restart");
    } catch (e) {
      line(e.message, "error");
      return;
    }
    line("Restarting Alfred's engine…");
    const status = await refreshStatus();  // an engine that can't start doesn't change the state: ask
    if (status && status.engine === "crashed") line(status.last_error || "Alfred's engine didn't start.", "error");
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight + 2, 160)}px`;
  }

  // --- approvals --------------------------------------------------------------
  const approvals = new Map();
  let current = null;
  let ticker = null;

  function addApproval(event) {
    const total = (Number(event.timeout) || 60) * 1000;
    const start = event.ts ? event.ts * 1000 : Date.now();
    const entry = {
      token: event.token, reason: event.reason || "do something that needs your OK", source: event.source,
      total, deadline: start + total,
    };
    approvals.set(event.token, entry);
    if (current && current.token === entry.token) current = entry;
    showApproval();
  }

  function closeApproval(token) {
    approvals.delete(token);
    if (current && current.token === token) current = null;
    showApproval();
  }

  function showApproval() {
    const dialog = $("approval");
    const now = Date.now();
    for (const [token, entry] of approvals) {
      if (entry.deadline + 3000 < now) approvals.delete(token);  // the server closes it; this is a fallback
    }
    if (current && approvals.has(current.token)) return;
    current = approvals.values().next().value || null;
    clearInterval(ticker);
    if (!current) {
      dialog.hidden = true;
      return;
    }
    $("approval-reason").textContent = current.reason;
    $("approval-from").textContent = ASKED[current.source] || `Asked from ${place(current.source)}.`;
    $("allow").disabled = $("deny").disabled = false;
    dialog.hidden = false;
    $("deny").focus();
    tick();
    ticker = setInterval(tick, 250);
  }

  function tick() {
    if (!current) return;
    const left = Math.max(0, current.deadline - Date.now());
    $("approval-bar").style.width = `${(left / current.total) * 100}%`;
    $("approval-time").textContent = left > 0
      ? `${Math.ceil(left / 1000)} s to answer. No answer means no.` : "Time's up: Alfred won't do it.";
    if (left <= 0) showApproval();
  }

  async function answer(approved) {
    if (!current) return;
    const entry = current;
    $("allow").disabled = $("deny").disabled = true;
    try {
      await api("POST", "/api/approval", { token: entry.token, approved });
    } catch (e) {
      line(e.message, "error");
    }
    closeApproval(entry.token);
  }

  // --- consent ----------------------------------------------------------------
  let consentVersion = null;
  async function showConsent() {
    $("consent").hidden = false;
    if (consentVersion !== null) return;
    try {
      const data = await api("GET", "/api/consent");
      consentVersion = data.version;
      renderMarkdown(data.notice_markdown || "", $("notice"));
      $("notice").focus();
    } catch (e) {
      $("consent-msg").textContent = e.message;
    }
  }

  async function agree() {
    const button = $("agree");
    button.disabled = true;
    try {
      await api("POST", "/api/consent", { version: consentVersion });
      $("consent").hidden = true;
      line("Thanks. Alfred is starting.");
      refreshVoice();
      input.focus();
    } catch (e) {
      $("consent-msg").textContent = e.message;
      consentVersion = null;
      showConsent();
    } finally {
      button.disabled = false;
    }
  }

  // A small, safe Markdown reader for the notice: headings, paragraphs, lists, tables, **bold**, `code`.
  function renderMarkdown(markdown, into) {
    into.textContent = "";
    let paragraph = [];
    let list = null;
    let table = null;
    const flush = () => {
      if (paragraph.length) into.append(inline(el("p"), paragraph.join(" ")));
      paragraph = [];
      list = null;
      table = null;
    };
    for (const raw of markdown.replace(/\r/g, "").split("\n")) {
      const text = raw.trim();
      let m;
      if (!text || /^(=|-|_){3,}$/.test(text)) {
        flush();
      } else if ((m = /^(#{1,6})\s+(.*)$/.exec(text))) {
        flush();
        into.append(inline(el(`h${Math.min(6, m[1].length + 1)}`), m[2]));
      } else if ((m = /^[-*]\s+(.*)$/.exec(text))) {
        if (!list) {
          flush();
          list = into.appendChild(el("ul"));
        }
        list.append(inline(el("li"), m[1]));
      } else if (text.startsWith("|")) {
        const cells = text.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (cells.every((c) => /^:?-+:?$/.test(c))) continue;
        if (!table) {
          flush();
          table = into.appendChild(el("table"));
          const row = table.appendChild(el("tr"));
          for (const cell of cells) row.append(inline(el("th"), cell));
          continue;
        }
        const row = table.appendChild(el("tr"));
        for (const cell of cells) row.append(inline(el("td"), cell));
      } else {
        if (list || table) flush();
        paragraph.push(text);
      }
    }
    flush();
  }

  function inline(node, text) {
    for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/)) {
      if (!part) continue;
      if (/^\*\*[^*]+\*\*$/.test(part)) node.append(el("strong", null, part.slice(2, -2)));
      else if (/^`[^`]+`$/.test(part)) node.append(el("code", null, part.slice(1, -1)));
      else if (/^\[[^\]]+\]\([^)]+\)$/.test(part)) {
        const m = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
        node.append(`${m[1]} (${m[2]})`);
      } else node.append(part.replace(/\*([^*]+)\*/g, "$1"));
    }
    return node;
  }

  // --- settings ---------------------------------------------------------------
  async function loadSettings() {
    try {
      settings = await api("GET", "/api/settings");
    } catch (e) {
      $("settings-msg").textContent = e.message;
      return;
    }
    const saved = settings.settings;
    const value = (key) => saved[key] || settings.defaults[key] || "";
    const fields = document.forms["settings-form"].elements;
    fields.JARVIS_MODEL.value = saved.JARVIS_MODEL || "";
    fields.JARVIS_MODEL.placeholder = settings.defaults.JARVIS_MODEL || "";
    fields.JARVIS_EFFORT.value = value("JARVIS_EFFORT");
    fields.JARVIS_COMPUTER_USE.checked = value("JARVIS_COMPUTER_USE") !== "0";
    fields.JARVIS_SPEAK.checked = value("JARVIS_SPEAK") !== "0";
    fields.JARVIS_NAME.value = saved.JARVIS_NAME || "";
    const voiceName = value("OPENAI_LIVE_VOICE");
    if (![...fields.OPENAI_LIVE_VOICE.options].some((option) => option.value === voiceName)) {
      fields.OPENAI_LIVE_VOICE.append(new Option(voiceName, voiceName));  // a voice set in settings.env
    }
    fields.OPENAI_LIVE_VOICE.value = voiceName;
    $("owner").value = saved.TELEGRAM_OWNER_ID || "";
    $("settings-file").textContent = settings.file;
    $("secrets-note").textContent = settings.secrets_saved
      ? "Keys are kept in Windows Credential Manager, protected by your Windows sign-in. Alfred never shows a saved key again, only its last few characters."
      : "This PC has no Credential Manager, so keys are kept only until Alfred closes. Alfred never shows a saved key again.";
    renderSecrets(settings.secrets);
    refreshVoice();  // a saved or removed OpenAI key turns voice mode on or off
  }

  async function saveSettings(event) {
    event.preventDefault();
    const message = $("settings-msg");
    if (!settings) return;
    const fields = document.forms["settings-form"].elements;
    const wanted = {
      JARVIS_MODEL: fields.JARVIS_MODEL.value.trim(),
      JARVIS_EFFORT: fields.JARVIS_EFFORT.value,
      JARVIS_COMPUTER_USE: fields.JARVIS_COMPUTER_USE.checked ? "1" : "0",
      JARVIS_SPEAK: fields.JARVIS_SPEAK.checked ? "1" : "0",
      JARVIS_NAME: fields.JARVIS_NAME.value.trim(),
      OPENAI_LIVE_VOICE: fields.OPENAI_LIVE_VOICE.value,
    };
    const changes = {};
    for (const [key, next] of Object.entries(wanted)) {
      const saved = settings.settings[key] || "";
      if (next === saved || (!saved && next === (settings.defaults[key] || ""))) continue;
      changes[key] = next;
    }
    if (!Object.keys(changes).length) {
      message.textContent = "Nothing changed.";
      return;
    }
    await putSettings(changes, message);
  }

  async function putSettings(changes, message) {
    message.className = "msg";
    try {
      const result = await api("PUT", "/api/settings", changes);
      await loadSettings();
      message.textContent = result.restarting
        ? "Saved. Alfred restarts to use it (after what it's doing now)." : "Saved.";
      return true;
    } catch (e) {
      message.className = "msg error";
      message.textContent = e.message;
      return false;
    }
  }

  function renderSecrets(map) {
    const box = $("secrets");
    const typed = {};  // keep what someone is pasting when the list is redrawn
    for (const field of box.querySelectorAll("input")) typed[field.dataset.key] = field.value;
    box.textContent = "";
    for (const [key, label, help] of SECRETS) {
      const info = map[key] || { set: false, hint: "" };
      const row = el("div", "secret");
      const field = el("label", "field");
      const entry = el("input");
      entry.type = "password";
      entry.autocomplete = "off";
      entry.spellcheck = false;
      entry.placeholder = info.set ? "Paste a new key to replace it" : "Paste the key";
      entry.dataset.key = key;
      entry.value = typed[key] || "";
      field.append(el("span", null, label), entry);
      const save = el("button", "primary", "Save");
      save.type = "button";
      const remove = el("button", null, "Remove");
      remove.type = "button";
      remove.disabled = !info.set;
      const state = el("div", `state${info.set ? " set" : ""}`, info.set ? `Saved: ${info.hint}` : `Not set. ${help}`);
      row.append(field, save, remove, state);
      box.append(row);

      save.onclick = async () => {
        if (!entry.value.trim()) { entry.focus(); return; }
        save.disabled = true;
        try {
          await api("PUT", `/api/secrets/${key}`, { value: entry.value });
          entry.value = "";
          await loadSettings();
        } catch (e) {
          state.className = "state";
          state.textContent = e.message;
          save.disabled = false;
        }
      };
      entry.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); save.click(); } };
      let armed = null;
      remove.onclick = async () => {
        if (!armed) {  // a second click within 4 s removes it
          remove.textContent = "Really remove?";
          armed = setTimeout(() => { armed = null; remove.textContent = "Remove"; }, 4000);
          return;
        }
        clearTimeout(armed);
        remove.disabled = true;
        try {
          await api("DELETE", `/api/secrets/${key}`);
          await loadSettings();
        } catch (e) {
          state.textContent = e.message;
        }
      };
    }
  }

  async function toggleTelegram() {
    const on = !["running", "starting", "discovery"].includes(telegramState);
    try {
      const result = await api("POST", "/api/telegram", { on });
      setTelegram(result.state, result.error);
    } catch (e) {
      $("telegram-state").textContent = e.message;
    }
  }

  // --- activity ---------------------------------------------------------------
  let audit = [];
  async function loadAudit() {
    try {
      const data = await api("GET", "/api/audit?limit=300");
      audit = data.entries || [];
      $("audit-file").textContent = data.file;
    } catch (e) {
      audit = [];
      $("audit-empty").textContent = e.message;
    }
    renderAudit();
  }

  function renderAudit() {
    const query = $("audit-filter").value.trim().toLowerCase();
    const rows = $("audit-rows");
    rows.textContent = "";
    let shown = 0;
    for (const entry of audit) {
      const args = entry.args && Object.keys(entry.args).length ? JSON.stringify(entry.args) : "";
      const result = String(entry.result === undefined || entry.result === null ? "" : entry.result);
      if (query && !`${entry.tool} ${args} ${result}`.toLowerCase().includes(query)) continue;
      const row = el("tr");
      const what = el("td");
      what.append(el("span", "tool", entry.tool || "?"));
      if (args) what.append(el("span", "args", clip(args, 240)));
      const outcome = el("td");
      outcome.append(el("span", entry.ok ? "good" : "bad", entry.ok ? "Done" : "Not done"), ` ${clip(result, 400)}`);
      row.append(el("td", null, String(entry.ts || "").replace("T", " ")), what, outcome);
      rows.append(row);
      shown += 1;
    }
    $("audit-empty").hidden = shown > 0;
  }

  const clip = (text, n) => (text.length > n ? `${text.slice(0, n)}…` : text);

  // --- voice mode -------------------------------------------------------------
  // Talk to Alfred like a phone call. The page connects to OpenAI's live voice model over WebRTC: the
  // microphone goes up, the voice comes back into <audio>, and the "oai-events" data channel carries
  // transcripts and delegations. The model only talks. Everything to do or find out it delegates to us:
  // we run what the user said on Alfred (/api/voice/delegate, source "voice", shown in the log like a
  // typed command) and hand the model Alfred's reply to say. STOP, the microphone button, closing the
  // window and reloading the page all end the call.
  const talk = $("talk");
  const talkState = $("talk-state");
  const voiceAudio = $("voice-audio");
  const TALK = { connecting: "Connecting…", listening: "Listening", working: "Alfred is working on it…" };
  const CLOSED = {
    expired: "Voice mode reached its time limit and ended.",
    content: "OpenAI ended voice mode.",
    connection_lost: "Voice mode lost its connection to OpenAI.",
  };
  const GRACE_MS = 300;  // the last words of a request can arrive just after its delegation
  const START_MS = 20000;  // OpenAI has this long to start the call
  const MAX_REQUEST = 4000;  // characters of what was said that go to Alfred
  let voiceInfo = { available: false, reason: "", message: "Checking whether voice mode is available…" };
  let live = null;  // the call: { pc, channel, mic, heard, pending, timer, ended }

  // off, unavailable, connecting, listening, working or error. note: the error, or why it's unavailable.
  function setTalk(state, note) {
    const on = state in TALK;
    talk.dataset.state = state;
    talk.setAttribute("aria-pressed", String(on));
    talk.setAttribute("aria-disabled", String(state === "unavailable"));
    talk.title = on ? `${TALK[state]} Press to end voice mode.` : note || "Talk to Alfred (voice mode)";
    talkState.textContent = on ? TALK[state] : state === "error" ? note : "";
    talkState.classList.toggle("error", state === "error");
    talkState.hidden = !talkState.textContent;
  }

  async function refreshVoice() {
    try { voiceInfo = await api("GET", "/api/voice/status"); } catch { return; }
    if (live) return;
    if (!voiceInfo.available) setTalk("unavailable", voiceInfo.message);
    else if (talk.dataset.state === "unavailable") setTalk("off");
  }

  function toggleVoice() {
    if (live) return endVoice("Voice mode is off.");
    if (!voiceInfo.available) {
      line(voiceInfo.message, "error");
      if (voiceInfo.reason === "key") showView("settings");
      return;
    }
    if (!navigator.mediaDevices || !window.RTCPeerConnection) {
      setTalk("error", "This window can't do voice mode. Open Alfred in its own window, or in Edge or Chrome.");
      return;
    }
    startVoice();
  }

  async function startVoice() {
    const me = live = { pc: null, channel: null, mic: null, heard: [], pending: [], timer: null, ended: false };
    setTalk("connecting");
    try {
      me.mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      if (live === me) endVoice(micProblem(e), true);
      return;
    }
    if (me.ended) return hangUp(me);  // ended while Windows asked about the microphone
    me.timer = setTimeout(() => {
      if (live === me) endVoice("OpenAI didn't start voice mode in time. Try again.", true);
    }, START_MS);
    const pc = me.pc = new RTCPeerConnection();
    pc.ontrack = (e) => {
      if (live !== me) return;
      voiceAudio.srcObject = e.streams[0];
      voiceAudio.play().catch(() => { /* the press that started voice mode lets it play */ });
    };
    pc.onconnectionstatechange = () => {
      if (live === me && pc.connectionState === "failed") endVoice(CLOSED.connection_lost, true);
    };
    for (const track of me.mic.getAudioTracks()) pc.addTrack(track, me.mic);
    const channel = me.channel = pc.createDataChannel("oai-events");
    channel.onmessage = (e) => {
      let event;
      try { event = JSON.parse(e.data); } catch { return; }
      if (live === me) onLive(me, event);
    };
    try {
      await pc.setLocalDescription(await pc.createOffer());
      const answer = await postOffer(pc.localDescription.sdp);
      if (!me.ended) await pc.setRemoteDescription({ type: "answer", sdp: answer });
    } catch (e) {
      if (live === me) endVoice(e.message, true);
    }
  }

  // The offer goes to Alfred's server, which adds the OpenAI key; OpenAI's answer comes back as SDP.
  async function postOffer(sdp) {
    let response;
    try {
      response = await fetch("/api/voice/session",
        { method: "POST", headers: { "X-Alfred": key, "Content-Type": "application/sdp" }, body: sdp });
    } catch {
      throw new Error("Alfred isn't answering. Is it still running?");
    }
    if (response.ok) return response.text();
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (response.status === 401) onSignedOut();
    throw new Error(data.error || `Alfred answered ${response.status}.`);
  }

  function micProblem(error) {
    const name = error && error.name;
    if (name === "NotAllowedError" || name === "SecurityError") {
      return "Alfred may not use the microphone. Allow it when Windows or this window asks, then try again.";
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone found. Plug one in, then try again.";
    if (name === "NotReadableError") return "Another app is using the microphone. Close it, then try again.";
    return "The microphone didn't start. Try again.";
  }

  function onLive(me, event) {
    switch (event.type) {
      case "session.started":
        clearTimeout(me.timer);
        setTalk(me.pending.length ? "working" : "listening");
        line("Voice mode is on. Talk to Alfred, and press the microphone again to end it.");
        break;
      case "session.input_transcript.delta":  // what the user says, in pieces
        if (event.delta) me.heard.push({ at: Number(event.start_ms), text: String(event.delta) });
        break;
      case "session.delegation.created":
        if (event.delegation && event.delegation.target === "client") {
          delegate(me, String(event.delegation.id), Number(event.offset_ms));
        }
        break;
      case "error":
        line(`Voice mode: ${(event.error && event.error.message) || "something went wrong."}`, "error");
        break;
      case "session.closed":
        endVoice(CLOSED[event.reason] || "Voice mode ended.", event.reason in CLOSED);
        break;
      default:
        break;
    }
  }

  // The model handed us a task. It carries no text: the request is what the user said up to that moment.
  async function delegate(me, id, offset) {
    me.pending.push(id);
    setTalk("working");
    tell(me, "thinking", id, "Alfred is working on it.");
    await new Promise((resolve) => { setTimeout(resolve, GRACE_MS); });
    if (me.ended) return;  // voice mode ended (STOP, the button) before this went to Alfred: drop it
    const text = takeRequest(me, offset);
    let answer;
    if (!text) {
      answer = "Alfred got no request: nothing was heard. Ask the user to say it again.";
    } else {
      try {
        const result = await api("POST", "/api/voice/delegate", { delegation_id: id, text });
        answer = `Alfred's answer: ${result.reply}`;
      } catch (e) {
        answer = `Alfred couldn't take the request: ${e.message}`;
      }
    }
    me.pending = me.pending.filter((pending) => pending !== id);
    if (live !== me) return;
    tell(me, "commentary", id, answer);
    setTalk(me.pending.length ? "working" : "listening");
  }

  // Everything said up to the delegation that Alfred hasn't had yet, so a request with a follow-up
  // ("email Sam" "saying what?" "that I'm late") reaches Alfred whole. Later words wait for the next one.
  function takeRequest(me, offset) {
    const said = [];
    me.heard = me.heard.filter((part) => {
      if (part.at > offset) return true;
      said.push(part.text);
      return false;
    });
    const text = said.join("").replace(/\s+/g, " ").trim();
    return text.length > MAX_REQUEST ? text.slice(-MAX_REQUEST) : text;
  }

  // Voice can't approve anything: the model asks the user to answer the dialog on screen.
  function voiceApproval(event) {
    if (!live) return;
    tell(live, "thinking", live.pending[0] || null, "Alfred is waiting for the user's OK on screen "
      + `before it will ${clip(String(event.reason || "go on"), 300)}. Ask the user to choose Allow or Deny in the Alfred window.`);
  }

  function sendLive(me, event) {
    const channel = me.channel;
    if (!channel || channel.readyState !== "open") return;
    try { channel.send(JSON.stringify(event)); } catch { /* closing */ }
  }

  // Context for the model, for one delegation or (null) the whole call: "thinking" is silent, and
  // "commentary" is for it to tell the user.
  function tell(me, kind, delegationId, content) {
    sendLive(me, { type: `session.${kind}.append`, content, delegation_id: delegationId });
  }

  function endVoice(message, failed) {
    const me = live;
    if (!me) return;
    live = null;
    hangUp(me);
    voiceAudio.srcObject = null;
    if (failed) setTalk("error", message);
    else setTalk(voiceInfo.available ? "off" : "unavailable", voiceInfo.message);
    if (message) line(message, failed ? "error" : undefined);
    if (document.hidden) onVisibility();  // a page nobody looks at lets go of its feed again
  }

  // The microphone stops at once; the connection a moment later, so session.close can reach OpenAI.
  // Safe to call again: it also lets go of what a start still in progress got after the call ended.
  function hangUp(me) {
    me.ended = true;
    clearTimeout(me.timer);
    if (me.mic) for (const track of me.mic.getTracks()) track.stop();
    const { pc } = me;
    if (!pc) return;
    sendLive(me, { type: "session.close" });
    me.pc = me.channel = null;
    setTimeout(() => pc.close(), 300);
  }

  // --- pages and keys -----------------------------------------------------------
  function showView(name) {
    for (const tab of document.querySelectorAll(".tab")) {
      if (tab.dataset.view === name) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
    }
    for (const view of document.querySelectorAll(".view")) view.hidden = view.id !== `view-${name}`;
    if (name === "settings") loadSettings();
    if (name === "activity") loadAudit();
    if (name === "home") {
      toEnd();  // what arrived while another page was open
      input.focus();
    }
  }

  function wire() {
    for (const tab of document.querySelectorAll(".tab")) tab.onclick = () => showView(tab.dataset.view);
    $("composer").onsubmit = send;
    input.addEventListener("input", autosize);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $("composer").requestSubmit();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        stopButton.focus();
      }
    });
    stopButton.onclick = stopEverything;
    talk.onclick = toggleVoice;
    $("reset").onclick = async () => {
      try { await api("POST", "/api/reset"); } catch (e) { line(e.message, "error"); }
    };
    $("allow").onclick = () => answer(true);
    $("deny").onclick = () => answer(false);
    $("agree").onclick = agree;
    $("settings-form").onsubmit = saveSettings;
    $("telegram").onclick = () => showView("settings");
    $("telegram-toggle").onclick = toggleTelegram;
    $("owner-save").onclick = () => {
      const owner = $("owner").value.trim();
      putSettings({ TELEGRAM_OWNER_ID: owner }, $("owner-msg"));
    };
    $("restart").onclick = restartEngine;
    $("quit").onclick = async () => {
      try {
        await api("POST", "/api/quit");
        signedOut = true;
        disconnect();
        setPill("offline");
        banner("conn", "Alfred is closing. You can close this window.", "error");
      } catch (e) {
        line(e.message, "error");
      }
    };
    $("audit-refresh").onclick = loadAudit;
    $("audit-filter").addEventListener("input", renderAudit);
  }

  wire();
  onVisibility();
  // Until the first-run setup (ui/onboarding/) is finished, it comes first.
  api("GET", "/api/onboarding/state").catch(() => ({})).then((setup) => {
    if (setup.complete === false) location.replace("onboarding/index.html");
    else refreshStatus().then(connect);
  });
  input.focus();
})();
