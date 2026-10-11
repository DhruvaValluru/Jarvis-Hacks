"use strict";
// Alfred's setup (jarvis/onboarding is the server side). Talks only to its own local server:
// /api/onboarding/*, /api/status and /api/stop. Text from the server, Telegram or a file is always set
// with textContent, never as HTML.

(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };

  const ENGINE = {
    off: "Not started", needs_consent: "Waiting for your OK", starting: "Starting", ready: "Ready", busy: "Working",
    stopping: "Stopping", crashed: "Crashed", offline: "Not connected",
  };
  const TELEGRAM = {
    off: "The bot is off.", not_configured: "The bot isn’t set up yet.", starting: "Starting the bot…",
    discovery: "Your bot is listening for the code.", running: "Your bot is on.", error: "The bot stopped.",
  };
  const SKIP = { anthropic: "Skip: simple commands only" };

  let state = null; // GET /api/onboarding/state
  let current = null; // the step on screen
  let known = null; // GET /api/onboarding/settings
  let consentVersion = null;
  let legacyText = null; // a chosen file's text, kept only until it is imported
  let pairingCode = "";
  let download = null; // the speech model download's last progress
  let signedOut = false;
  const timers = {};

  // --- server -------------------------------------------------------------------------------
  // The page key app.js got from /auth lives in sessionStorage, readable only by this origin
  // (127.0.0.1 at Alfred's port). Every call sends it; there is no cookie.
  const key = (() => {
    try { return sessionStorage.getItem("alfred-key") || ""; } catch { return ""; }
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
    if (!response.ok) {
      const error = new Error(data.error || `Alfred answered ${response.status}.`);
      error.data = data;
      throw error;
    }
    return data;
  }

  function onSignedOut() {
    if (signedOut) return;
    signedOut = true;
    for (const name of Object.keys(timers)) stopPoll(name);
    banner("This window isn't signed in to Alfred any more (Alfred may have restarted). Open Alfred again from "
      + "its tray icon or the Start menu.");
    for (const button of document.querySelectorAll("button")) button.disabled = true;
  }

  function banner(text) {
    const box = $("banner");
    box.hidden = !text;
    box.textContent = text || "";
  }

  // --- small helpers ------------------------------------------------------------------------
  function note(id, text, kind) {
    const box = $(id);
    box.hidden = !text;
    box.className = `aero-note aero-note--${kind || "info"}`;
    box.textContent = text || "";
    return box;
  }

  function message(text, kind) {
    const box = $("msg");
    box.className = `msg${kind ? ` ${kind}` : ""}`;
    box.textContent = text || "";
  }

  async function busy(button, label, work) {
    const was = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
      return await work();
    } finally {
      button.disabled = signedOut;
      button.textContent = was;
    }
  }

  function poll(name, fn, ms) {
    stopPoll(name);
    const tick = async () => {
      let again = false;
      try { again = await fn(); } catch { again = !signedOut; }
      if (again && !signedOut) timers[name] = setTimeout(tick, ms);
      else delete timers[name];
    };
    timers[name] = setTimeout(tick, ms);
  }

  function stopPoll(name) {
    clearTimeout(timers[name]);
    delete timers[name];
  }

  const megabytes = (bytes) => `${Math.round((bytes || 0) / 1e6)} MB`;
  const info = (name) => state.steps.find((step) => step.name === name);
  const names = () => state.steps.map((step) => step.name);

  // --- steps and navigation -------------------------------------------------------------------
  // A step opens once every required step before it is done (welcome, the notice, safety).
  function reachable(name) {
    for (const step of state.steps) {
      if (step.name === name) return true;
      if (step.required && step.status !== "done") return false;
    }
    return false;
  }

  function nextAfter(name) {
    const all = names();
    for (let i = all.indexOf(name) + 1; i < all.length; i += 1) {
      if (info(all[i]).available) return all[i];
    }
    return "finish";
  }

  function previous(name) {
    const all = names();
    for (let i = all.indexOf(name) - 1; i >= 0; i -= 1) {
      if (info(all[i]).available) return all[i];
    }
    return all[0];
  }

  function stepState(step) {
    if (step.name === "speech" && download && download.state === "downloading") {
      return download.percent === null ? "Downloading" : `Downloading ${download.percent}%`;
    }
    if (step.status === "done") return "Done";
    if (step.status === "skipped") return "Skipped";
    if (!step.available) return "Not needed";
    return step.required ? "" : "Optional";
  }

  function renderSteps() {
    const list = $("steps");
    list.textContent = "";
    state.steps.forEach((step, i) => {
      const item = el("li", "aero-steps__item");
      item.dataset.name = step.name;
      item.dataset.status = step.name === current ? "current" : step.status || "todo";
      const button = el("button", "aero-steps__button");
      button.type = "button";
      button.disabled = signedOut || !reachable(step.name);
      if (step.name === current) button.setAttribute("aria-current", "step");
      const shown = step.name === current ? "" : step.status;
      const mark = el("span", "aero-steps__mark", shown === "done" ? "✓" : shown === "skipped" ? "–" : String(i + 1));
      mark.setAttribute("aria-hidden", "true");
      button.append(mark, el("span", "aero-steps__title", step.title), el("span", "aero-steps__state", stepState(step)));
      button.onclick = () => go(step.name);
      item.append(button);
      list.append(item);
    });
    $("to-app").hidden = !state.complete;
  }

  function requiredDone() {
    return state.steps.every((step) => !step.required || step.name === "finish" || step.status === "done");
  }

  function renderFooter() {
    const step = info(current);
    const next = $("next");
    $("back").hidden = names().indexOf(current) === 0;
    $("skip").hidden = step.required;
    $("skip").textContent = SKIP[current] || "Skip";
    $("skip-rest").hidden = state.complete || step.required || !requiredDone();
    next.className = `aero-button ${current === "finish" ? "aero-button--green" : "aero-button--primary"}`;
    if (current === "consent") next.textContent = step.ready ? "Next" : "I agree";
    else if (current === "safety") next.textContent = "Got it";
    else if (current === "import") next.textContent = step.status === "done" ? "Next" : "Next without importing";
    else if (current === "finish") next.textContent = state.complete ? "Back to Alfred" : "Done";
    else next.textContent = "Next";
    // Optional steps are "done" once their key or code is saved; until then, Skip.
    next.disabled = signedOut || (["welcome", "anthropic", "openai", "telegram", "pcs"].includes(current) && !step.ready);
  }

  async function refreshState() {
    state = await api("GET", "/api/onboarding/state");
    renderSteps();
    renderFooter();
  }

  async function go(name, { focus = true } = {}) {
    if (!reachable(name)) name = state.current;
    current = name;
    history.replaceState(null, "", `#${name}`);
    for (const section of document.querySelectorAll(".step")) section.hidden = section.dataset.step !== name;
    const index = names().indexOf(name);
    $("step-count").textContent = `Step ${index + 1} of ${names().length}: ${info(name).title}`;
    $("progress-bar").style.width = `${((index + 1) / names().length) * 100}%`;
    $("progress").setAttribute("aria-valuemax", String(names().length));
    $("progress").setAttribute("aria-valuenow", String(index + 1));
    message("");
    renderSteps();
    renderFooter();
    if (focus) {
      const heading = document.querySelector(`.step[data-step="${name}"] h1`);
      if (heading) heading.focus();
    }
    try {
      await (STEPS[name].enter || (() => null))();
    } catch (e) {
      message(e.message, "error");
    }
    renderFooter();
  }

  async function mark(name, status) {
    state = await api("POST", "/api/onboarding/step", { step: name, status });
  }

  async function next() {
    const name = current;
    const button = $("next");
    if (name === "finish" && state.complete) {
      location.href = "../index.html";
      return;
    }
    try {
      await busy(button, "One moment…", async () => {
        const step = STEPS[name];
        if (step.leave) await step.leave();
        if (step.next) await step.next();
        else await mark(name, "done");
      });
      if (name === "finish") {
        location.href = "../index.html";
        return;
      }
      await go(nextAfter(name));
    } catch (e) {
      message(e.message, "error");
      renderFooter();
    }
  }

  async function skip() {
    const name = current;
    try {
      if (STEPS[name].leave) await STEPS[name].leave();
      await mark(name, "skipped");
      await go(nextAfter(name));
    } catch (e) {
      message(e.message, "error");
    }
  }

  async function skipRest() {
    try {
      if (STEPS[current].leave) await STEPS[current].leave();
      for (const step of state.steps) {
        if (!step.required && !step.status && step.available) await mark(step.name, "skipped");
      }
      await go("finish");
    } catch (e) {
      message(e.message, "error");
    }
  }

  async function loadKnown() {
    known = await api("GET", "/api/onboarding/settings");
    return known;
  }

  function showSaved(id, secret, label) {
    const box = $(id);
    box.hidden = !secret.set;
    box.textContent = secret.set ? `${label}: ${secret.hint}. Paste a new one to replace it.` : "";
  }

  // --- 1. welcome -----------------------------------------------------------------------------
  async function enterWelcome() {
    const list = $("checks");
    const data = await api("GET", "/api/onboarding/system");
    list.textContent = "";
    for (const check of data.checks) {
      const kind = check.ok === true ? "yes" : check.ok === null ? "unknown" : check.name === "windows" && data.blocking ? "bad" : "no";
      const item = el("li", `check ${kind}`);
      const icon = el("span", "check-icon", { yes: "✓", no: "–", bad: "✕", unknown: "?" }[kind]);
      icon.setAttribute("aria-hidden", "true");
      const text = el("span", "check-text");
      text.append(el("strong", null, check.label), el("span", null, check.detail));
      item.append(icon, text);
      list.append(item);
    }
    $("os-block").hidden = !data.blocking;
    $("os-block").textContent = data.blocking ? "Setup can’t go on here: Alfred runs on Windows 10 (version 1809 or later) and Windows 11." : "";
  }

  // --- 2. data notice ---------------------------------------------------------------------------
  async function enterConsent() {
    const data = await api("GET", "/api/onboarding/consent");
    consentVersion = data.version;
    renderMarkdown(data.notice_markdown || "", $("notice"));
    $("consent-done").hidden = !data.accepted;
  }

  async function agree() {
    if (info("consent").ready) {
      await mark("consent", "done");
      return;
    }
    try {
      const result = await api("POST", "/api/onboarding/consent", { version: consentVersion });
      state = result.state;
    } catch (e) {
      await enterConsent(); // the notice changed: show the new one
      throw e;
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

  // --- 3. old settings ---------------------------------------------------------------------------
  async function enterImport() {
    const data = await api("GET", "/api/onboarding/legacy");
    $("legacy-found").hidden = !data.found;
    $("legacy-none").hidden = data.found;
    $("legacy-path").textContent = data.path;
    if (legacyText === null) showLegacyKeys(data.keys);
  }

  function showLegacyKeys(keys) {
    const rows = $("legacy-rows");
    rows.textContent = "";
    for (const key of keys) {
      const row = el("tr");
      row.append(el("td", "key", key.name), el("td", key.secret ? "secret" : null, key.value));
      if (key.import === false) row.className = "left-out";
      rows.append(row);
    }
    $("legacy-table").hidden = !keys.length;
    $("legacy-import").disabled = !keys.some((key) => key.import !== false);
  }

  async function chooseLegacy() {
    const input = $("legacy-file");
    const file = input.files && input.files[0];
    input.value = ""; // choosing the same file again still counts
    if (!file) return;
    if (file.size > 64 * 1024) {
      note("legacy-msg", "That file is too big to be Alfred's old settings.", "error");
      return;
    }
    try {
      const text = await file.text();
      const data = await api("POST", "/api/onboarding/legacy/preview", { text });
      legacyText = text;
      $("legacy-file-name").textContent = file.name;
      showLegacyKeys(data.keys);
      note("legacy-msg", data.keys.length ? "" : "No settings found in that file.", "warn");
    } catch (e) {
      note("legacy-msg", e.message, "error");
    }
  }

  async function importLegacy() {
    try {
      const result = await busy($("legacy-import"), "Importing…",
        () => api("POST", "/api/onboarding/legacy/import", legacyText === null ? {} : { text: legacyText }));
      legacyText = null; // forget the file's text
      state = result.state;
      const leftOut = result.left_out && result.left_out.length
        ? ` Left out, because they look secret and Alfred doesn’t use them: ${result.left_out.join(", ")}.` : "";
      note("legacy-msg", `Imported ${result.imported.length} settings: ${result.imported.join(", ")}.${leftOut}`
        + (result.restarting ? " Alfred restarts its engine to use them." : ""), "ok");
      renderSteps();
      renderFooter();
    } catch (e) {
      note("legacy-msg", e.message, "error");
    }
  }

  // --- 4. Anthropic and 7. OpenAI --------------------------------------------------------------------
  async function enterAnthropic() {
    await loadKnown();
    showSaved("anthropic-saved", known.secrets.ANTHROPIC_API_KEY, "A key is saved");
    $("legacy-hint").hidden = info("import").status === "done" || known.secrets.ANTHROPIC_API_KEY.set;
  }

  async function checkAnthropic(anyway) {
    const field = $("anthropic-key");
    if (!field.value.trim()) {
      note("anthropic-msg", "Paste your Anthropic API key first.", "error");
      field.focus();
      return;
    }
    try {
      const result = await busy($("anthropic-check"), "Checking…",
        () => api("POST", "/api/onboarding/anthropic", { key: field.value, save_anyway: anyway === true }));
      field.value = "";
      field.removeAttribute("aria-invalid");
      $("anthropic-anyway").hidden = true;
      note("anthropic-msg", result.message + (result.restarting ? " Alfred restarts its engine to use it." : ""), "ok");
      await refreshState();
      await enterAnthropic();
    } catch (e) {
      const result = e.data && e.data.result;
      field.setAttribute("aria-invalid", result === "rejected" || result === "format" ? "true" : "false");
      // Only when Anthropic couldn't answer (no connection, busy or down), never for a key it turned down.
      $("anthropic-anyway").hidden = !(result === "offline" || result === "unavailable");
      note("anthropic-msg", e.message, "error");
    }
  }

  async function enterOpenai() {
    await loadKnown();
    showSaved("openai-saved", known.secrets.OPENAI_API_KEY, "A key is saved");
  }

  async function checkOpenai() {
    const field = $("openai-key");
    if (!field.value.trim()) {
      note("openai-msg", "Paste your OpenAI API key first.", "error");
      field.focus();
      return;
    }
    try {
      const result = await busy($("openai-check"), "Checking…",
        () => api("POST", "/api/onboarding/openai", { key: field.value }));
      field.value = "";
      field.removeAttribute("aria-invalid");
      note("openai-msg", result.message, "ok");
      await refreshState();
      await enterOpenai();
    } catch (e) {
      field.setAttribute("aria-invalid", "true");
      note("openai-msg", e.message, "error");
    }
  }

  // --- 5. safety ---------------------------------------------------------------------------------------
  async function enterSafety() {
    await loadKnown();
    $("computer-use").checked = known.computer_use;
  }

  async function saveSafety() {
    const result = await api("POST", "/api/onboarding/safety", { computer_use: $("computer-use").checked });
    state = result.state;
  }

  // --- 6. voice --------------------------------------------------------------------------------------------
  let model = "base.en";
  let modelPresent = false;

  async function enterSpeech() {
    const data = await api("GET", "/api/onboarding/speech");
    const select = $("mic");
    select.textContent = "";
    select.append(new Option("Windows’ default microphone", ""));
    for (const device of data.microphones.devices) {
      select.append(new Option(device.default ? `${device.name} (Windows’ default)` : device.name, device.name));
    }
    select.value = data.microphones.devices.some((d) => d.name === data.selected) ? data.selected : "";
    select.disabled = !data.microphones.available;
    $("mic-msg").hidden = !data.microphones.error;
    $("mic-msg").textContent = data.microphones.error;
    model = data.model;
    modelPresent = data.present;
    showDownload(data.download);
    $("voice-test").dataset.supported = data.voice_test ? "1" : "0";
    if (data.download.state === "downloading") watchDownload();
  }

  function showDownload(progress) {
    download = progress;
    const phase = progress.state;
    const downloading = phase === "downloading";
    if (phase === "done") modelPresent = true;
    $("model-state").textContent = modelPresent
      ? `The speech model (${model}) is on this PC. Alfred loads it from here.`
      : `The speech model (${model}) isn't on this PC yet.`;
    $("download-box").hidden = phase === "idle";
    $("download").hidden = downloading;
    $("download").textContent = modelPresent ? "Download it again" : phase === "cancelled" || phase === "error"
      ? "Continue the download" : "Download the speech model";
    $("download-cancel").hidden = !downloading;
    const box = $("download-progress");
    box.classList.toggle("aero-progress--indeterminate", downloading && progress.percent === null);
    const percent = phase === "done" ? 100 : progress.percent || 0;
    $("download-bar").style.width = `${percent}%`;
    box.setAttribute("aria-valuenow", String(percent));
    let text = "";
    if (downloading) {
      text = progress.total ? `Downloading: ${megabytes(progress.bytes)} of about ${megabytes(progress.total)}.`
        : `Downloading: ${megabytes(progress.bytes)} so far.`;
    } else if (phase === "done") text = "Downloaded.";
    else if (phase === "cancelled") text = "Cancelled. Downloading again continues where it stopped.";
    else if (phase === "error") text = progress.error;
    $("download-text").textContent = text;
    // The download keeps going on other steps; the step list shows how far it got.
    const label = document.querySelector('.aero-steps__item[data-name="speech"] .aero-steps__state');
    if (label) label.textContent = stepState(info("speech"));
  }

  function watchDownload() {
    poll("download", async () => {
      showDownload(await api("GET", "/api/onboarding/speech/download"));
      return download.state === "downloading";
    }, 700);
  }

  async function startDownload() {
    try {
      showDownload(await api("POST", "/api/onboarding/speech/download"));
      watchDownload();
    } catch (e) {
      message(e.message, "error");
    }
  }

  async function cancelDownload() {
    try {
      showDownload(await api("DELETE", "/api/onboarding/speech/download"));
      stopPoll("download");
    } catch (e) {
      message(e.message, "error");
    }
  }

  async function chooseMic() {
    try {
      await api("PUT", "/api/onboarding/microphone", { name: $("mic").value });
      message("Microphone saved.");
    } catch (e) {
      message(e.message, "error");
    }
  }

  async function voiceTest() {
    const button = $("voice-test");
    if (button.dataset.supported === "1") note("voice-result", "Listening… say: “What time is it?”", "info");
    try {
      const result = await busy(button, "Listening…", () => api("POST", "/api/onboarding/speech/test"));
      if (!result.supported) {
        note("voice-result", result.message, "info");
        return;
      }
      const box = note("voice-result", "", "ok");
      box.hidden = false;
      box.append(el("p", null, `Alfred heard: “${result.heard || "(nothing)"}”`),
        el("p", null, `Alfred: ${result.reply || "(no reply)"}`));
    } catch (e) {
      note("voice-result", e.message, "error");
    }
  }

  // --- 8. Telegram -----------------------------------------------------------------------------------------
  async function enterTelegram() {
    await loadKnown();
    showSaved("bot-saved", known.secrets.TELEGRAM_BOT_TOKEN, "A bot token is saved");
    showSaved("spectrum-saved", known.secrets.SPECTRUM_PROJECT_SECRET, "A secret is saved");
    if (known.bot && known.bot.username) showBot(known.bot);
    const owner = known.telegram_owner;
    $("owner-saved").hidden = !owner;
    $("owner-saved").textContent = owner ? `Linked to Telegram account ${owner}. Only that account can use your bot.` : "";
    $("link-start").textContent = owner ? "Link a different account" : "Show a code";
    const link = await api("GET", "/api/onboarding/telegram/link");
    showLink(link);
    if (link.state === "waiting" && !timers.link) watchLink();
  }

  function showBot(bot) {
    const link = $("bot-link");
    if (!/^https:\/\/t\.me\/[A-Za-z0-9_]{3,64}$/.test(bot.link || "")) return;
    link.textContent = `@${bot.username}`;
    link.href = bot.link;
    $("bot-found").hidden = false;
  }

  async function checkBot() {
    const field = $("bot-token");
    if (!field.value.trim()) {
      note("bot-msg", "Paste the bot token from @BotFather first.", "error");
      field.focus();
      return;
    }
    try {
      const result = await busy($("bot-check"), "Checking…",
        () => api("POST", "/api/onboarding/telegram/token", { token: field.value }));
      field.value = "";
      note("bot-msg", result.message, "ok");
      showBot(result);
      await enterTelegram();
    } catch (e) {
      note("bot-msg", e.message, "error");
    }
  }

  async function saveSpectrum() {
    const field = $("spectrum-secret");
    if (!field.value.trim()) {
      note("spectrum-msg", "Paste the Photon Spectrum project secret first.", "error");
      field.focus();
      return;
    }
    try {
      const result = await busy($("spectrum-save"), "Saving…",
        () => api("POST", "/api/onboarding/telegram/spectrum", { secret: field.value }));
      field.value = "";
      note("spectrum-msg", result.message, "ok");
      await enterTelegram();
    } catch (e) {
      note("spectrum-msg", e.message, "error");
    }
  }

  function botName() {
    return known && known.bot && known.bot.username ? `@${known.bot.username}` : "your bot";
  }

  function showLink(link) {
    const waiting = link.state === "waiting";
    $("link-box").hidden = !waiting;
    $("link-start").hidden = waiting;
    if (waiting) {
      const code = $("link-code");
      code.textContent = `${link.code.slice(0, 3)} ${link.code.slice(3)}`;
      code.setAttribute("aria-label", `Code: ${link.code.split("").join(" ")}`);
      const minutes = Math.floor(link.expires_in / 60);
      const seconds = String(link.expires_in % 60).padStart(2, "0");
      let text = `Send this code to ${botName()} from your own Telegram account, as a message on its own. `
        + `It works for ${minutes}:${seconds} more. ${TELEGRAM[link.telegram] || ""}`;
      if (link.telegram === "error" && link.telegram_error) text += ` ${link.telegram_error}`;
      if (link.wrong) text += ` ${link.wrong} wrong ${link.wrong === 1 ? "code has" : "codes have"} been sent to your bot.`;
      $("link-text").textContent = text;
      note("link-msg", "");
      return;
    }
    if (link.state === "linked") {
      note("link-msg", `Linked: only Telegram account ${link.owner} can use your bot now. Send it /start to say hello.`, "ok");
    } else if (link.state === "expired") {
      note("link-msg", "The code expired before it reached your bot. Show a new one.", "warn");
    } else if (link.state === "locked") {
      note("link-msg", "Too many wrong codes were sent to your bot, so that code no longer works. Show a new one.", "error");
    } else if (link.state === "cancelled") {
      note("link-msg", "Cancelled. Nothing changed: your bot is back as it was.", "info");
    } else {
      note("link-msg", "");
    }
  }

  function watchLink() {
    poll("link", async () => {
      const link = await api("GET", "/api/onboarding/telegram/link");
      showLink(link);
      if (link.state === "linked") {
        await refreshState();
        await enterTelegram();
      }
      return link.state === "waiting";
    }, 1500);
  }

  async function startLink() {
    try {
      showLink(await busy($("link-start"), "Starting…", () => api("POST", "/api/onboarding/telegram/link")));
      watchLink();
    } catch (e) {
      note("link-msg", e.message, "error");
    }
  }

  async function cancelLink() {
    stopPoll("link");
    try {
      showLink(await busy($("link-cancel"), "Cancelling…", () => api("DELETE", "/api/onboarding/telegram/link")));
    } catch (e) {
      note("link-msg", e.message, "error");
    }
  }

  // --- 9. other PCs --------------------------------------------------------------------------------------------
  let pcSaved = { name: "", serve: false };

  async function enterPcs() {
    await loadKnown();
    $("pc-name").value = known.name;
    $("pc-name").placeholder = known.default_name || "";
    $("pc-serve").checked = known.serve;
    pcSaved = { name: known.name, serve: known.serve };
    const token = known.secrets.JARVIS_TOKEN;
    $("pair-saved").hidden = !token.set;
    $("pair-saved").textContent = token.set ? `This PC has a pairing code (${token.hint}).` : "";
  }

  function showPairing(result) {
    pairingCode = result.pairing_code;
    $("pair-shown").textContent = result.grouped;
    $("pair-box").hidden = false;
    note("pair-msg", "");
  }

  async function newPairing(replace) {
    try {
      const result = await busy($("pair-new"), "Making a code…",
        () => api("POST", "/api/onboarding/pcs/new", replace === true ? { replace: true } : {}));
      showPairing(result);
      await refreshState();
      await enterPcs();
    } catch (e) {
      const box = note("pair-msg", e.message, e.data && e.data.result === "exists" ? "warn" : "error");
      if (e.data && e.data.result === "exists") {
        const again = el("button", "aero-button aero-button--small", "Make a new code anyway");
        again.type = "button";
        again.onclick = () => newPairing(true);
        box.append(" ", again);
      }
    }
  }

  async function joinPairing() {
    const field = $("pair-code");
    try {
      const result = await busy($("pair-join"), "Joining…",
        () => api("POST", "/api/onboarding/pcs/join", { code: field.value }));
      field.value = "";
      $("pair-box").hidden = true;
      note("pair-msg", result.message, "ok");
      await refreshState();
      await enterPcs();
    } catch (e) {
      note("pair-msg", e.message, "error");
    }
  }

  async function copyPairing() {
    if (!pairingCode) return;
    try {
      await navigator.clipboard.writeText(pairingCode);
    } catch {
      const range = document.createRange(); // older WebView: copy the selected code
      range.selectNodeContents($("pair-shown"));
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("copy");
    }
    note("pair-msg", "Copied. Paste it on your other PCs in Alfred’s setup (Other PCs, then Join).", "ok");
  }

  async function savePc({ quiet = false } = {}) {
    const wanted = { name: $("pc-name").value.trim(), serve: $("pc-serve").checked };
    if (wanted.name === pcSaved.name && wanted.serve === pcSaved.serve) {
      if (!quiet) $("pc-msg").textContent = "Nothing changed.";
      return;
    }
    const result = await api("PUT", "/api/onboarding/pcs", wanted);
    pcSaved = wanted;
    $("pc-msg").textContent = result.restarting ? "Saved. Alfred restarts its engine to use it." : "Saved.";
  }

  // --- 10. finish ----------------------------------------------------------------------------------------------
  async function enterFinish() {
    await loadKnown();
    const [auto, voice] = await Promise.all([
      api("GET", "/api/onboarding/autostart"), api("GET", "/api/onboarding/speech").catch(() => null),
    ]);
    const lines = [
      [known.secrets.ANTHROPIC_API_KEY.set, "Claude", known.secrets.ANTHROPIC_API_KEY.set
        ? "Connected." : "No key, so Alfred only understands simple commands. Add one any time in Settings."],
      [known.computer_use, "Mouse and keyboard", known.computer_use ? "Alfred may use them when it needs to." : "Off."],
      [Boolean(voice && voice.present), "Voice", voice && voice.present ? "The speech model is on this PC." : "Not set up."],
      [Boolean(known.telegram_owner), "Telegram", known.telegram_owner ? "Linked to your account." : "Not set up."],
      [known.secrets.JARVIS_TOKEN.set, "Other PCs", known.secrets.JARVIS_TOKEN.set
        ? (known.serve ? "Paired. Your other PCs may use this one." : "Paired.") : "Not set up."],
    ];
    const list = $("summary");
    list.textContent = "";
    for (const [ok, label, detail] of lines) {
      const item = el("li", `check ${ok ? "yes" : "no"}`);
      const icon = el("span", "check-icon", ok ? "✓" : "–");
      icon.setAttribute("aria-hidden", "true");
      const text = el("span", "check-text");
      text.append(el("strong", null, label), el("span", null, detail));
      item.append(icon, text);
      list.append(item);
    }
    showAutostart(auto);
  }

  function showAutostart(auto) {
    const box = $("autostart");
    box.checked = auto.enabled;
    box.disabled = !auto.supported;
    if (!auto.supported) $("autostart-help").textContent = "Only on Windows.";
    else if (auto.simulated) $("autostart-help").textContent = "The stand-in engine (--fake-core) only pretends: "
      + "Windows’ startup apps don’t change.";
  }

  async function setAutostart() {
    const box = $("autostart");
    try {
      showAutostart(await api("PUT", "/api/onboarding/autostart", { on: box.checked }));
      $("autostart-msg").textContent = box.checked ? "Alfred starts when you sign in." : "Alfred doesn’t start by itself.";
    } catch (e) {
      box.checked = !box.checked;
      $("autostart-msg").textContent = e.message;
    }
  }

  async function tryIt() {
    note("try-result", "Asking Alfred to open Notepad…", "info");
    try {
      const result = await busy($("try"), "Working…", () => api("POST", "/api/onboarding/try"));
      const box = note("try-result", "", result.ok ? "ok" : "warn");
      box.hidden = false;
      for (const reply of result.replies) box.append(el("p", null, `Alfred: ${reply}`));
      if (result.timed_out) box.append(el("p", null, "Alfred is still working on it. You’ll see the rest in the main window."));
      else if (result.stopped) box.append(el("p", null, "Stopped."));
      else if (!result.replies.length) box.append(el("p", null, result.ok ? "Done." : "That didn’t work this time."));
    } catch (e) {
      note("try-result", e.message, "error");
    }
  }

  // --- header -------------------------------------------------------------------------------------------------------
  async function showEngine() {
    let engine = "offline";
    try {
      engine = (await api("GET", "/api/status")).engine;
    } catch { /* shown as not connected */ }
    const badge = $("engine");
    const kind = engine === "ready" || engine === "busy" ? " aero-badge--ok"
      : engine === "crashed" || engine === "offline" ? " aero-badge--warn" : "";
    badge.className = `aero-badge engine${kind}`;
    badge.textContent = `Alfred: ${ENGINE[engine] || engine}`;
    return true;
  }

  function watchEngine() {
    showEngine();
    poll("engine", showEngine, 3000);
  }

  async function stopEverything() {
    try {
      const result = await api("POST", "/api/stop");
      message(result.stopped ? "Stopped. Alfred let go of the mouse and keyboard." : "Nothing to stop: Alfred isn’t running.");
    } catch (e) {
      message(e.message, "error");
    }
  }

  // --- wiring ------------------------------------------------------------------------------------------------------
  const STEPS = {
    welcome: { enter: enterWelcome },
    consent: { enter: enterConsent, next: agree },
    import: {
      enter: enterImport,
      next: async () => { if (info("import").status !== "done") await mark("import", "skipped"); },
    },
    anthropic: { enter: enterAnthropic },
    safety: { enter: enterSafety, next: saveSafety },
    speech: { enter: enterSpeech },
    openai: { enter: enterOpenai },
    telegram: { enter: enterTelegram },
    pcs: { enter: enterPcs, leave: () => savePc({ quiet: true }) },
    finish: { enter: enterFinish },
  };

  function wire() {
    $("next").onclick = next;
    $("back").onclick = () => go(previous(current));
    $("skip").onclick = skip;
    $("skip-rest").onclick = skipRest;
    $("stop").onclick = stopEverything;
    for (const link of document.querySelectorAll("[data-go]")) link.onclick = () => go(link.dataset.go);
    for (const field of document.querySelectorAll("[data-enter]")) {
      field.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.isComposing) {
          e.preventDefault();
          $(field.dataset.enter).click();
        }
      });
    }
    $("legacy-choose").onclick = () => $("legacy-file").click();
    $("legacy-file").onchange = chooseLegacy;
    $("legacy-import").onclick = importLegacy;
    $("anthropic-check").onclick = () => checkAnthropic(false);
    $("anthropic-anyway").onclick = () => checkAnthropic(true);
    $("openai-check").onclick = checkOpenai;
    $("mic").onchange = chooseMic;
    $("download").onclick = startDownload;
    $("download-cancel").onclick = cancelDownload;
    $("voice-test").onclick = voiceTest;
    $("bot-check").onclick = checkBot;
    $("spectrum-save").onclick = saveSpectrum;
    $("link-start").onclick = startLink;
    $("link-cancel").onclick = cancelLink;
    $("pair-new").onclick = () => newPairing(false);
    $("pair-join").onclick = joinPairing;
    $("pair-copy").onclick = copyPairing;
    $("pc-save").onclick = async () => {
      try { await savePc(); } catch (e) { $("pc-msg").textContent = e.message; }
    };
    $("autostart").onchange = setAutostart;
    $("try").onclick = tryIt;
  }

  async function start() {
    wire();
    try {
      state = await api("GET", "/api/onboarding/state");
    } catch (e) {
      banner(e.message);
      return;
    }
    const wanted = decodeURIComponent(location.hash.slice(1));
    await go(names().includes(wanted) && reachable(wanted) ? wanted : state.current, { focus: false });
    watchEngine();
  }

  start();
})();
