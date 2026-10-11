"use strict";
// Alfred's web demo: a stand-in for Alfred's local server, in the page. It runs before app.js and
// onboarding.js, wraps fetch, and answers every /api/* call they make with the same shapes and status
// codes as the real one (windows-mvp/jarvis/app/server.py, app/voice.py, onboarding/routes.py),
// including the event feed: a streamed text/event-stream body. Any other fetch passes through.
// There is no server, no AI and no network: scenarios.js scripts what Alfred does and desktop.js shows
// it on the simulated PC. State lives in this page; the setup's progress, settings and key hints are
// also kept in sessionStorage, so the setup and the main window agree.

(() => {
  const Demo = window.AlfredDemo = window.AlfredDemo || {};
  const DATA = window.AlfredDemoData || { version: "0.1.0", terms: "", notice: "" };
  const TEST = window.AlfredDemoTest || {}; // the e2e script may speed things up
  const SPEED = Number(TEST.speed) || 1;
  const nativeFetch = window.fetch.bind(window);
  const script = document.currentScript;
  const onSetup = Boolean(script && script.dataset.page === "setup"); // build.py marks the setup page
  Demo.page = onSetup ? "setup" : "main";

  // --- small helpers -----------------------------------------------------------------------------
  const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
  const hex = (n) => {
    const bytes = new Uint8Array(Math.ceil(n / 2));
    crypto.getRandomValues(bytes);
    return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, n);
  };
  const pad = (n) => String(n).padStart(2, "0");
  const isoNow = (ms = Date.now()) => {
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };
  const clone = (value) => JSON.parse(JSON.stringify(value));

  class HTTPError extends Error {
    constructor(status, message, extra) {
      super(message);
      this.status = status;
      this.extra = extra || {};
    }
  }
  class Stopped extends Error {}
  class ToolFail extends Error {}
  Demo.Stopped = Stopped;
  Demo.ToolFail = ToolFail;

  // settings.hint: "sk-ant-api03-...9f2c" -> "sk-ant-...9f2c", enough to recognise a key.
  function hint(secret) {
    if (!secret) return "";
    if (secret.length < 12) return "(set)";
    const prefix = /^[A-Za-z]{2,4}-(?:[A-Za-z]{2,5}-)?/.exec(secret);
    return `${prefix ? prefix[0] : ""}...${secret.slice(-4)}`;
  }

  // Python's repr() of tool arguments, as the engine prints them ("-> open_app {'name': 'Notepad'}").
  function pyRepr(value) {
    if (value === null || value === undefined) return "None";
    if (value === true) return "True";
    if (value === false) return "False";
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return pyStr(value);
    if (Array.isArray(value)) return `[${value.map(pyRepr).join(", ")}]`;
    return `{${Object.entries(value).map(([k, v]) => `${pyStr(k)}: ${pyRepr(v)}`).join(", ")}}`;
  }
  function pyStr(text) {
    const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
    let out = text.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t");
    if (quote === "'") out = out.replace(/'/g, "\\'");
    return quote + out + quote;
  }
  // Python's json.dumps(args, ensure_ascii=False): what an approval for a "confirm" tool shows.
  function pyJson(value) {
    if (Array.isArray(value)) return `[${value.map(pyJson).join(", ")}]`;
    if (value && typeof value === "object") {
      return `{${Object.entries(value).map(([k, v]) => `${JSON.stringify(k)}: ${pyJson(v)}`).join(", ")}}`;
    }
    return value === undefined ? "null" : JSON.stringify(value);
  }
  Demo.pyRepr = pyRepr;
  Demo.pyJson = pyJson;

  // --- what is kept between pages (sessionStorage) --------------------------------------------------
  const STORE = "alfred-demo";
  const STEPS = ["welcome", "consent", "import", "anthropic", "safety", "speech", "openai", "telegram", "pcs", "finish"];
  const REQUIRED = new Set(["welcome", "consent", "safety", "finish"]);
  const TITLES = {
    welcome: "Welcome", consent: "Your data", import: "Old settings", anthropic: "Claude", safety: "Safety",
    speech: "Voice", openai: "OpenAI", telegram: "Telegram", pcs: "Other PCs", finish: "Finish",
  };

  // A PC that went through the setup two days ago: Claude connected, paired with the laptop.
  function setUpPC() {
    const then = Date.now() - 2 * 86400000;
    return {
      settings: { JARVIS_COMPUTER_USE: "1", JARVIS_NAME: "Desktop" },
      hints: { ANTHROPIC_API_KEY: "sk-ant-...4f2c", JARVIS_TOKEN: "...Q7wz" },
      setup: {
        steps: { welcome: "done", consent: "done", anthropic: "done", safety: "done", speech: "skipped",
          openai: "skipped", telegram: "skipped", pcs: "done", finish: "done" },
        completed_at: isoNow(then),
      },
      consent: true, autostart: true, mic: "", model: false, before: null,
    };
  }
  // A new PC, for "Replay the setup".
  function newPC() {
    return { settings: {}, hints: {}, setup: { steps: {}, completed_at: null }, consent: false, autostart: false,
      mic: "", model: false, before: null };
  }

  function load() {
    try {
      const data = JSON.parse(sessionStorage.getItem(STORE) || "null");
      if (data && data.setup && data.settings && data.hints) return data;
    } catch { /* storage blocked or bad: start again */ }
    return setUpPC();
  }
  let saved = load();
  function save() {
    try { sessionStorage.setItem(STORE, JSON.stringify(saved)); } catch { /* private window: this page only */ }
  }

  // The artifact host picks light or dark with data-theme on <html> of the main page; the setup page,
  // a page of its own, keeps the same choice.
  try {
    const theme = document.documentElement.getAttribute("data-theme");
    if (!onSetup && theme) sessionStorage.setItem("alfred-demo-theme", theme);
    else if (!onSetup) sessionStorage.removeItem("alfred-demo-theme");
    else if (!theme && sessionStorage.getItem("alfred-demo-theme")) {
      document.documentElement.setAttribute("data-theme", sessionStorage.getItem("alfred-demo-theme"));
    }
  } catch { /* no storage: follow the system */ }

  if (!onSetup && !saved.setup.completed_at) {
    // Back from a replay that wasn't finished: the main window opens on the set-up PC again.
    saved = saved.before || setUpPC();
    saved.before = null;
    save();
  }

  // --- settings, as the app's Store has them ------------------------------------------------------
  const SECRET_KEYS = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "JARVIS_TOKEN", "OPENAI_API_KEY",
    "SPECTRUM_PROJECT_SECRET", "TELEGRAM_BOT_TOKEN", "VOICE_SERVICE_TOKEN"];
  const FLAG = /^[01]$/;
  const SETTINGS = {
    JARVIS_MODEL: /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,99}$/, JARVIS_EFFORT: /^(low|medium|high)$/, JARVIS_SPEAK: FLAG,
    JARVIS_COMPUTER_USE: FLAG, JARVIS_NAME: /^[A-Za-z0-9][A-Za-z0-9 _-]{0,29}$/,
    JARVIS_WHISPER_MODEL: /^[a-z0-9][a-z0-9.-]{0,39}$/, JARVIS_MAX_STEPS: /^([1-9][0-9]?|1[0-9][0-9]|200)$/,
    JARVIS_FALLBACKS: FLAG, TELEGRAM_OWNER_ID: /^[0-9]{1,20}$/, OPENAI_LIVE_VOICE: /^[a-z][a-z0-9_-]{0,39}$/,
    OPENAI_LIVE_MODEL: /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,99}$/,
  };
  const LABELS = {
    JARVIS_MODEL: "Model", JARVIS_EFFORT: "Effort (low, medium or high)", JARVIS_SPEAK: "Spoken replies",
    JARVIS_COMPUTER_USE: "Computer use", JARVIS_NAME: "PC name (letters, digits, spaces, - and _)",
    JARVIS_WHISPER_MODEL: "Speech model", JARVIS_MAX_STEPS: "Max steps (1 to 200)", JARVIS_FALLBACKS: "Fallbacks",
    TELEGRAM_OWNER_ID: "Telegram owner ID (digits)", OPENAI_LIVE_VOICE: "Voice mode voice",
    OPENAI_LIVE_MODEL: "Voice mode model",
  };
  const DEFAULTS = {
    JARVIS_MODEL: "claude-opus-5-5", JARVIS_EFFORT: "low", JARVIS_SPEAK: "1", JARVIS_COMPUTER_USE: "1",
    JARVIS_WHISPER_MODEL: "base.en", JARVIS_FALLBACKS: "1", OPENAI_LIVE_MODEL: "gpt-live-1", OPENAI_LIVE_VOICE: "marin",
  };
  const TELEGRAM_KEYS = new Set(["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "SPECTRUM_PROJECT_ID",
    "SPECTRUM_PROJECT_SECRET", "OPENAI_API_KEY", "OPENAI_TTS_VOICE", "OPENAI_TTS_MODEL", "OPENAI_TRANSCRIBE_MODEL",
    "VOICE_SERVICE_URL", "VOICE_SERVICE_TOKEN"]);
  const VOICE_KEYS = new Set(["OPENAI_LIVE_MODEL", "OPENAI_LIVE_VOICE"]);
  const PLACE = "C:\\Users\\Alex\\AppData\\Local\\Jarvis";
  const KEPT = "(simulated: kept in this page)";
  const DEFAULT_NAME = "DESKTOP-7Q2K4F";
  const VOICE_OFF = "Voice mode runs in the Windows app (it needs your microphone and an OpenAI key).";
  // app.js shows it as "Telegram: <this>"
  const TELEGRAM_OFF = "only in the Windows app, with your own bot. This demo doesn't connect to Telegram.";

  const has = (key) => Boolean(saved.hints[key]) || (key === "TELEGRAM_OWNER_ID" && Boolean(saved.settings.TELEGRAM_OWNER_ID));
  const setting = (key) => saved.settings[key] || DEFAULTS[key] || "";
  const pcName = () => saved.settings.JARVIS_NAME || DEFAULT_NAME;
  const computerUse = () => setting("JARVIS_COMPUTER_USE") !== "0";
  function setSecret(key, value) {
    saved.hints[key] = hint(value);
    save();
  }

  // --- the event feed (service/events.py) -------------------------------------------------------------
  const feed = { seq: 0, events: [], streams: new Set() };
  function publish(type, fields, ts) {
    const event = { ...fields, seq: ++feed.seq, ts: (ts || Date.now()) / 1000, type };
    feed.events.push(event);
    if (feed.events.length > 1000) feed.events.shift();
    for (const send of [...feed.streams]) send(event);
    return event;
  }
  function since(after) {
    if (after === null || after > feed.seq) return [...feed.events];
    return feed.events.filter((event) => event.seq > after);
  }

  // GET /api/events: every kept event after ?after=N, then new ones as they happen, with a keep-alive
  // comment when nothing happened for 15 s. Aborting the fetch ends the stream, as on the real server.
  function eventStream(after, signal) {
    const encoder = new TextEncoder();
    let send = null;
    let keepAlive = null;
    const end = () => {
      if (send) feed.streams.delete(send);
      clearInterval(keepAlive);
      send = null;
    };
    const body = new ReadableStream({
      start(controller) {
        send = (event) => controller.enqueue(encoder.encode(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`));
        for (const event of since(after)) send(event);
        feed.streams.add(send);
        keepAlive = setInterval(() => controller.enqueue(encoder.encode(": keep-alive\n\n")), 15000);
        if (signal) {
          signal.addEventListener("abort", () => {
            end();
            try { controller.error(new DOMException("The feed was closed.", "AbortError")); } catch { /* closed */ }
          }, { once: true });
        }
      },
      cancel: end,
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" } });
  }

  // --- the engine (service/core.py and the engine process, played by scenarios.js) ----------------------
  const engine = {
    base: saved.consent ? "ready" : "needs_consent", gen: 0, commands: new Map(), running: null,
    approvals: new Map(), shown: "", timers: new Set(), restarting: false, boot: null,
  };

  // The engine process starting: "ready" after a moment. A newer start (or a stop) replaces it.
  function boot(afterStop, ms) {
    clearTimeout(engine.boot);
    engine.boot = setTimeout(() => ready(afterStop), ms / SPEED);
  }

  function status() {
    const ids = [...engine.commands.keys()];
    const busy = engine.base === "ready" && ids.length > 0;
    return {
      engine: busy ? "busy" : engine.base, busy_id: busy ? ids[0] : null, queued: busy ? ids.length - 1 : 0,
      has_llm: has("ANTHROPIC_API_KEY") || !onSetup, model: setting("JARVIS_MODEL"), computer_use: computerUse(),
      pc_name: pcName(), telegram: telegramState(),
      approvals: [...engine.approvals.values()].map(({ token, reason, timeout, id, source }) => ({ token, reason, timeout, id, source })),
      last_error: engine.base === "needs_consent"
        ? "Alfred's data notice hasn't been accepted on this PC yet. At the PC, run: jarvis.bat --terms" : "",
    };
  }

  function publishState() {
    const st = status();
    const key = `${st.engine}|${st.busy_id}|${st.queued}`;
    if (key === engine.shown) return;
    engine.shown = key;
    publish("state", { state: st.engine, busy_id: st.busy_id, queued: st.queued });
  }

  function notReadyText() {
    if (engine.base === "starting" || engine.base === "stopping") return "Alfred is restarting. Send that again in a few seconds.";
    if (engine.base === "needs_consent") return "Alfred's data notice hasn't been accepted on this PC yet.";
    return "Alfred isn't running on the PC right now.";
  }

  function ready(afterStop) {
    engine.base = "ready";
    publish("ready", { after_stop: afterStop, released: 0, version: DATA.version, has_llm: true,
      computer_use: computerUse(), name: pcName(), protocol: 2 });
    publishState();
    pump();
  }

  function start() {
    if (engine.base !== "needs_consent" && engine.base !== "off") return;
    if (!saved.consent) {
      engine.base = "needs_consent";
      publishState();
      return;
    }
    engine.base = "starting";
    publishState();
    boot(false, 700);
  }

  function submit(text, source = "desktop") {
    text = String(text || "").trim();
    if (!text) throw new HTTPError(400, "The command is empty.");
    if (text.length > 10000) throw new HTTPError(400, "That command is too long.");
    if (engine.base !== "ready") throw new HTTPError(409, notReadyText());
    if (engine.commands.size >= 20) throw new HTTPError(429, "The command queue is full. Wait for a reply before sending more.");
    let done;
    const finished = new Promise((resolve) => { done = resolve; });
    const cmd = { id: hex(12), text, source, replies: [], ok: false, stopped: false, finished, done };
    engine.commands.set(cmd.id, cmd);
    publish("command", { id: cmd.id, source, text });
    publishState();
    publish("accepted", { id: cmd.id });
    setTimeout(pump, 0);
    return cmd;
  }

  // One command at a time, like the engine. Stop and restart end a run by changing engine.gen.
  async function pump() {
    if (engine.running || engine.base !== "ready") return;
    const cmd = engine.commands.values().next().value;
    if (!cmd) return;
    engine.running = cmd;
    const gen = engine.gen;
    const run = makeRun(cmd, gen);
    let ok = true;
    try {
      if (!Demo.scenarios) throw new Error("The demo's scenarios didn't load.");
      ok = (await Demo.scenarios.run(cmd.text, run)) !== false;
    } catch (e) {
      if (e instanceof Stopped || gen !== engine.gen) return;
      console.error(e);
      ok = false;
      run.reply(`Command failed (${e.name || "Error"}). Alfred's log on the PC has the details.`);
    }
    if (gen !== engine.gen) return; // stopped or restarted meanwhile: that reported the command
    engine.running = null;
    engine.commands.delete(cmd.id);
    cmd.ok = ok;
    publish("done", { id: cmd.id, ok, source: cmd.source, stopped: false });
    cmd.done();
    publishState();
    if (engine.restarting) finishRestart();
    else setTimeout(pump, 0);
  }

  // What a scenario gets: the engine's outputs (status, reply, approval), paced and stoppable.
  function makeRun(cmd, gen) {
    const run = {
      id: cmd.id, source: cmd.source, // (run.text, below, is the brain's "text" status line)
      get pc() { return Demo.desktop; },
      get stopped() { return gen !== engine.gen; },
      check() { if (gen !== engine.gen) throw new Stopped(); },
      async pace(ms) {
        await sleep(ms / SPEED);
        run.check();
      },
      status(kind, text) {
        run.check();
        publish("status", { id: cmd.id, kind, text });
      },
      async think() {
        run.status("thinking", "thinking...");
        await run.pace(650);
      },
      text(text) { run.status("text", text); },
      reply(text) {
        run.check();
        cmd.replies.push(text);
        publish("reply", { id: cmd.id, source: cmd.source, text });
      },
      notice(text) {
        run.check();
        publish("notice", { text });
      },
      // One tool call: the brain's status line, an approval first if the tool needs one, the work,
      // and its line in the activity log. perform() returns the tool's result or throws ToolFail.
      async tool(name, args, perform, options = {}) {
        if (options.status !== false) run.status("tool", `-> ${name} ${pyRepr(args)}`);
        const auditName = options.audit || name;
        const auditArgs = options.auditArgs || args;
        await run.pace(options.lead === undefined ? 380 : options.lead);
        if (options.approve) {
          if (options.before) await options.before();
          const yes = await approve(cmd, gen, options.approve);
          run.check();
          if (!yes) {
            writeAudit(auditName, auditArgs, false, options.declined || "declined by user");
            return { ok: false, declined: true, result: "" };
          }
        }
        let ok = true;
        let result;
        try {
          result = await perform();
        } catch (e) {
          if (!(e instanceof ToolFail)) throw e;
          ok = false;
          result = e.message;
        }
        run.check();
        writeAudit(auditName, typeof auditArgs === "function" ? auditArgs() : auditArgs, ok,
          result === undefined || result === null ? "Done." : result);
        await run.pace(options.after === undefined ? 260 : options.after);
        return { ok, result };
      },
      // A timer in the engine (set_timer): it dies with the engine, as on a real Stop.
      later(seconds, fn) {
        const timer = setTimeout(() => {
          engine.timers.delete(timer);
          if (gen === engine.gen) fn();
        }, (seconds * 1000) / SPEED);
        engine.timers.add(timer);
      },
      announce(text) { publish("announce", { text, source: cmd.source }); },
    };
    return run;
  }

  // engine.Approvals: one token per request; the first answer wins; no answer in time means no.
  function approve(cmd, gen, reason) {
    return new Promise((resolve, reject) => {
      const timeout = Number(TEST.approvalTimeout) || 60;
      const entry = { token: hex(8), reason, timeout, id: cmd.id, source: cmd.source, resolve, reject, gen };
      entry.timer = setTimeout(() => closeApproval(entry.token, false, "timeout"), timeout * 1000);
      engine.approvals.set(entry.token, entry);
      publish("approval", { token: entry.token, reason, timeout, id: cmd.id, source: cmd.source });
      publishState();
    });
  }

  function closeApproval(token, approved, by, error) {
    const entry = engine.approvals.get(token);
    if (!entry) return false;
    engine.approvals.delete(token);
    clearTimeout(entry.timer);
    publish("approval_closed", { token, approved, by });
    publishState();
    if (error) entry.reject(error);
    else entry.resolve(approved);
    return true;
  }

  // A hard stop: the engine and everything it started end at once, then a fresh one starts.
  function stop(source) {
    if (engine.base === "needs_consent" || engine.base === "off") return false;
    if (engine.base === "stopping") return true;
    engine.gen += 1;
    engine.base = "stopping";
    engine.restarting = false;
    clearTimeout(engine.boot);
    publish("stop", { source });
    publishState();
    if (Demo.desktop) Demo.desktop.halt();
    for (const timer of engine.timers) clearTimeout(timer);
    engine.timers.clear();
    setTimeout(() => {
      for (const token of [...engine.approvals.keys()]) closeApproval(token, false, "stop", new Stopped());
      for (const cmd of engine.commands.values()) {
        cmd.stopped = true;
        publish("done", { id: cmd.id, ok: false, source: cmd.source, stopped: true });
        cmd.done();
      }
      engine.commands.clear();
      engine.running = null;
      engine.base = "starting";
      publishState();
      boot(true, 550);
    }, 300 / SPEED);
    return true;
  }

  // restart_engine: finish the command that runs, drop the queued ones, start a fresh engine.
  function restart() {
    if (!saved.consent) {
      engine.base = "needs_consent";
      publishState();
      return;
    }
    if (engine.base === "stopping" || engine.restarting) return;
    if (engine.base !== "ready") {
      start();
      return;
    }
    engine.restarting = true;
    engine.base = "starting";
    publishState();
    if (!engine.running) finishRestart();
  }

  function finishRestart() {
    engine.restarting = false;
    engine.gen += 1;
    for (const timer of engine.timers) clearTimeout(timer);
    engine.timers.clear();
    for (const token of [...engine.approvals.keys()]) closeApproval(token, false, "restart", new Stopped());
    for (const cmd of engine.commands.values()) {
      publish("done", { id: cmd.id, ok: false, source: cmd.source, stopped: false });
      cmd.done();
    }
    engine.commands.clear();
    engine.running = null;
    engine.base = "starting";
    publishState();
    boot(false, 650);
  }

  // --- the activity log (tools/registry.py audit) ------------------------------------------------------------
  const audit = [];
  function writeAudit(tool, args, ok, result, ts) {
    audit.push({ ts: isoNow(ts), tool, args, ok, result: String(result).slice(0, 500) });
  }
  Demo.writeAudit = writeAudit;

  // --- Telegram ---------------------------------------------------------------------------------------------------
  let linking = null; // the setup's Telegram link: { state, code, deadline, owner, timer }
  function telegramState() {
    if (linking && linking.state === "waiting") return "discovery";
    return has("TELEGRAM_BOT_TOKEN") && has("SPECTRUM_PROJECT_SECRET") ? "off" : "not_configured";
  }

  // --- routes --------------------------------------------------------------------------------------------------------
  const routes = [];
  function add(method, path, handler) {
    routes.push({ method, pattern: new RegExp(`^${path.replace(/\{(\w+)\}/g, "(?<$1>[^/]+)")}$`), handler });
  }

  function apiStatus() {
    return { ...status(), consent: saved.consent, telegram_error: "",
      hotkey: { keys: "Ctrl+Alt+Backspace", ok: true, error: "" }, version: DATA.version, quit: false };
  }

  add("GET", "/api/health", () => ({ ok: true, app: "Alfred" }));
  add("GET", "/api/status", apiStatus);
  add("POST", "/api/command", ({ body }) => {
    if (typeof body.text !== "string" || !body.text.trim()) throw new HTTPError(400, "Type what you want Alfred to do first.");
    return { id: submit(body.text, "desktop").id };
  });
  add("POST", "/api/stop", () => ({ stopped: stop("desktop") }));
  add("POST", "/api/approval", ({ body }) => {
    if (typeof body.token !== "string" || !body.token) throw new HTTPError(400, "Which approval? The token is missing.");
    if (!closeApproval(body.token, body.approved === true, "desktop")) {
      throw new HTTPError(409, "That approval expired or was already answered.");
    }
    return { ok: true };
  });
  add("POST", "/api/reset", () => {
    if (engine.base !== "ready") throw new HTTPError(409, "Alfred isn't running right now.");
    setTimeout(() => publish("notice", { text: "Okay, fresh start." }), 120 / SPEED);
    return { ok: true };
  });
  add("POST", "/api/restart", () => {
    restart();
    return { ok: true };
  });
  add("POST", "/api/quit", () => { throw new HTTPError(409, "Close Alfred where you started it."); });

  add("GET", "/api/settings", () => {
    const secrets = {};
    for (const key of SECRET_KEYS) secrets[key] = { set: Boolean(saved.hints[key]), hint: saved.hints[key] || "" };
    return {
      settings: { ...saved.settings }, editable: Object.keys(SETTINGS), defaults: DEFAULTS, secrets,
      secrets_saved: false, file: `${PLACE}\\settings.env ${KEPT}`, telegram: saved.settings.JARVIS_TELEGRAM !== "0",
    };
  });
  add("PUT", "/api/settings", ({ body }) => {
    if (!Object.keys(body).length) throw new HTTPError(400, "Nothing to save.");
    const changes = {};
    for (const [key, raw] of Object.entries(body)) {
      if (SECRET_KEYS.includes(key)) throw new HTTPError(400, "Save API keys and tokens under API keys.");
      if (!(key in SETTINGS)) throw new HTTPError(400, `${key} can't be changed from the app.`);
      const value = text(key, raw);
      if (value && !SETTINGS[key].test(value)) throw new HTTPError(400, `${LABELS[key]}: that value doesn't look right.`);
      changes[key] = value;
    }
    const changed = Object.keys(changes).filter((key) => (saved.settings[key] || "") !== changes[key]);
    for (const key of changed) {
      if (changes[key]) saved.settings[key] = changes[key];
      else delete saved.settings[key]; // an empty value goes back to the default
    }
    save();
    return { saved: changed.sort(), restarting: apply(changed) };
  });
  add("PUT", "/api/secrets/{name}", ({ body, params }) => {
    secretName(params.name);
    if (typeof body.value !== "string" || !body.value.trim()) throw new HTTPError(400, "Paste the key first.");
    setSecret(params.name, body.value.trim());
    return { set: true, hint: saved.hints[params.name], restarting: apply([params.name]) };
  });
  add("DELETE", "/api/secrets/{name}", ({ params }) => {
    secretName(params.name);
    const had = Boolean(saved.hints[params.name]);
    delete saved.hints[params.name];
    save();
    return { set: false, hint: "", restarting: had ? apply([params.name]) : false };
  });
  add("POST", "/api/telegram", ({ body }) => {
    const on = body.on === true;
    saved.settings.JARVIS_TELEGRAM = on ? "1" : "0";
    save();
    return { state: telegramState(), error: on ? TELEGRAM_OFF : "" };
  });
  add("GET", "/api/audit", ({ query }) => {
    const limit = Math.min(Math.max(parseInt(query.get("limit"), 10) || 200, 1), 1000);
    return { entries: audit.slice(-limit).reverse(), file: `${PLACE}\\audit.jsonl ${KEPT}` };
  });
  add("GET", "/api/consent", () => ({ accepted: saved.consent, version: DATA.terms, notice_markdown: DATA.notice }));
  add("POST", "/api/consent", ({ body }) => {
    agree(body.version);
    return { accepted: true, status: apiStatus() };
  });

  // voice mode (app/voice.py): only in the Windows app
  add("GET", "/api/voice/status", () => ({ available: false, reason: "demo", message: VOICE_OFF }));
  add("POST", "/api/voice/session", () => { throw new HTTPError(409, VOICE_OFF); });
  add("POST", "/api/voice/delegate", () => { throw new HTTPError(409, VOICE_OFF); });

  function text(key, value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "boolean") return value ? "1" : "0";
    if (typeof value === "number" && Number.isInteger(value)) return String(value);
    if (typeof value !== "string") throw new HTTPError(400, `${LABELS[key]}: that value doesn't look right.`);
    return value.trim();
  }
  function secretName(name) {
    if (!SECRET_KEYS.includes(name)) throw new HTTPError(404, "No such key.");
  }
  // Restart what reads the changed settings, as the app does. True if the engine restarts.
  function apply(keys) {
    const restarts = keys.some((key) => !TELEGRAM_KEYS.has(key) && !VOICE_KEYS.has(key));
    if (restarts && engine.base !== "needs_consent") restart();
    return restarts && engine.base !== "needs_consent";
  }
  function agree(version) {
    if (version !== null && version !== undefined && version !== DATA.terms) {
      throw new HTTPError(409, "The notice changed while it was open. Read the new one, then agree.");
    }
    saved.consent = true;
    save();
    start();
  }

  // --- the setup (onboarding/routes.py) -------------------------------------------------------------------------
  const API = "/api/onboarding";
  const NOT_READY = {
    welcome: "Alfred needs Windows 10 (version 1809 or later) or Windows 11.",
    consent: "Read the notice and press I agree first.",
    anthropic: "Save an Anthropic API key first, or skip this step.",
    openai: "Save an OpenAI API key first, or skip this step.",
    telegram: "Save the bot token and the Photon secret, then link your account. Or skip this step.",
    pcs: "Make or paste a pairing code first, or skip this step.",
    finish: "Read the notice and press I agree first.",
  };
  const KEYLIKE = /^[\x21-\x7e]{20,400}$/;
  const BOT_TOKEN = /^[0-9]{5,15}:[A-Za-z0-9_-]{30,60}$/;
  const SPECTRUM = /^[\x21-\x7e]{8,500}$/;
  const PC_NAME = /^[A-Za-z0-9][A-Za-z0-9 _-]{0,29}$/;
  const MICS = [
    { name: "Microphone Array (Realtek(R) Audio)", default: true },
    { name: "Headset Microphone (USB Audio Device)", default: false },
  ];
  const clean = (value) => String(value || "").trim().replace(/^['"]+|['"]+$/g, "").trim();

  function stepReady(name) {
    if (name === "welcome") return true;
    if (name === "consent" || name === "finish") return saved.consent;
    if (name === "anthropic") return has("ANTHROPIC_API_KEY");
    if (name === "openai") return has("OPENAI_API_KEY");
    if (name === "telegram") return has("TELEGRAM_BOT_TOKEN") && has("SPECTRUM_PROJECT_SECRET") && has("TELEGRAM_OWNER_ID");
    if (name === "pcs") return has("JARVIS_TOKEN");
    return true;
  }
  function view() {
    const steps = STEPS.map((name) => ({
      name, title: TITLES[name], status: saved.setup.steps[name] || "", required: REQUIRED.has(name),
      available: name !== "import" || name in saved.setup.steps, ready: stepReady(name),
    }));
    const next = steps.find((step) => !step.status && step.available);
    return { version: 1, complete: Boolean(saved.setup.completed_at), completed_at: saved.setup.completed_at || null,
      current: next ? next.name : "finish", steps };
  }
  function mark(name, value) {
    saved.setup.steps[name] = value;
    if (name === "finish" && value === "done") {
      saved.setup.completed_at = isoNow();
      if (saved.before) {
        // The end of a replay: keep what was set in it, and what the PC had before for the rest.
        for (const [key, value_] of Object.entries(saved.before.hints)) if (!saved.hints[key]) saved.hints[key] = value_;
        for (const [key, value_] of Object.entries(saved.before.settings)) if (!(key in saved.settings)) saved.settings[key] = value_;
        saved.before = null;
      }
    }
    save();
  }
  function failed(result, message, extra) {
    return new HTTPError(422, message, { ok: false, result, message, ...(extra || {}) });
  }
  function current() {
    const secrets = {};
    for (const key of ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET", "JARVIS_TOKEN"]) {
      secrets[key] = { set: Boolean(saved.hints[key]), hint: saved.hints[key] || "" };
    }
    return { secrets, secrets_saved: false, computer_use: computerUse(), name: saved.settings.JARVIS_NAME || "",
      default_name: DEFAULT_NAME, serve: saved.settings.JARVIS_SERVE === "1",
      telegram_owner: saved.settings.TELEGRAM_OWNER_ID || "", telegram: telegramState(), bot: {}, engine: status().engine };
  }

  add("GET", `${API}/state`, view);
  add("POST", `${API}/step`, ({ body }) => {
    const { step, status: value } = body;
    if (!STEPS.includes(step)) throw new HTTPError(404, "No such setup step.");
    if (value !== "done" && value !== "skipped") throw new HTTPError(400, "A step is either done or skipped.");
    if (value === "skipped" && REQUIRED.has(step)) throw new HTTPError(409, `${TITLES[step]} can't be skipped.`);
    if (value === "done" && !stepReady(step)) throw new HTTPError(409, NOT_READY[step] || "That step isn't finished yet.");
    if (value === "done" && step === "finish") {
      const missing = STEPS.filter((name) => REQUIRED.has(name) && name !== "finish" && saved.setup.steps[name] !== "done");
      if (missing.length) throw new HTTPError(409, `Go through these steps first: ${missing.map((n) => TITLES[n]).join(", ")}.`);
    }
    mark(step, value);
    return view();
  });
  add("GET", `${API}/system`, () => ({
    supported: true, blocking: false, os: "Windows 11 Pro (build 26100)",
    checks: [
      { name: "windows", label: "Windows", ok: true, detail: "Windows 11 Pro (build 26100), simulated. Alfred works here." },
      { name: "microphone", label: "Microphone", ok: true, detail: "Found one. You can talk to Alfred." },
      { name: "office", label: "Microsoft Office", ok: true, detail: "Found Word or Excel. Alfred can read and fill in your documents." },
      { name: "tailscale", label: "Tailscale", ok: false,
        detail: "Not found. Only needed to use your PCs across different networks; on the same Wi-Fi it isn't." },
    ],
  }));
  add("GET", `${API}/consent`, () => ({ accepted: saved.consent, version: DATA.terms, notice_markdown: DATA.notice }));
  add("POST", `${API}/consent`, ({ body }) => {
    agree(body.version);
    mark("consent", "done");
    return { accepted: true, state: view() };
  });
  add("GET", `${API}/settings`, current);

  // old settings: nothing found on the simulated PC; a file the viewer chooses is read in this page only
  add("GET", `${API}/legacy`, () => ({ found: false, path: "", keys: [] }));
  add("POST", `${API}/legacy/preview`, ({ body }) => ({ keys: listing(envValues(envText(body.text))) }));
  add("POST", `${API}/legacy/import`, ({ body }) => {
    if (body.text === undefined || body.text === null) {
      throw new HTTPError(404, "Alfred didn't find old settings next to it. Choose the file instead.");
    }
    const imported = [];
    const leftOut = [];
    for (const [key, value] of Object.entries(envValues(envText(body.text)))) {
      if (!/^[A-Z][A-Z0-9_]*$/.test(key)) continue;
      if (!SECRET_KEYS.includes(key) && /KEY|TOKEN|SECRET|PASSWORD|PASSWD/.test(key)) {
        leftOut.push(key);
        continue;
      }
      if (SECRET_KEYS.includes(key)) saved.hints[key] = hint(value);
      else saved.settings[key] = value;
      imported.push(key);
    }
    if (!imported.length) throw new HTTPError(400, "That file has no settings Alfred can import.");
    mark("import", "done");
    return { imported, left_out: leftOut, restarting: false, state: view() };
  });
  function envText(value) {
    if (typeof value !== "string" || !value.trim()) throw new HTTPError(400, "That file is empty.");
    if (value.length > 64 * 1024) throw new HTTPError(400, "That file is too big to be Alfred's old settings.");
    return value;
  }
  function envValues(textValue) {
    const values = {};
    for (const raw of textValue.replace(/\r/g, "").split("\n")) {
      const line = raw.trim().replace(/^export\s+/, "");
      const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
      if (!m || line.startsWith("#")) continue;
      values[m[1]] = m[2].replace(/\s+#.*$/, "").replace(/^(['"])(.*)\1$/, "$2");
    }
    return values;
  }
  function listing(values) {
    return Object.entries(values).filter(([key]) => /^[A-Z][A-Z0-9_]*$/.test(key)).map(([key, value]) => {
      if (SECRET_KEYS.includes(key)) return { name: key, secret: true, value: hint(value), import: true };
      if (!/KEY|TOKEN|SECRET|PASSWORD|PASSWD/.test(key)) return { name: key, secret: false, value: value.slice(0, 80), import: true };
      return { name: key, secret: true, value: "(hidden, not imported)", import: false };
    });
  }

  // keys: the shape is checked as in validators.py; the online check is the Windows app's
  function keyCheck(value, service, site) {
    const key = clean(value);
    if (!key) throw failed("empty", `Paste your ${service} API key first.`);
    if (!KEYLIKE.test(key)) throw failed("format", `That doesn't look like an API key. Copy it again from ${site}.`);
    return key;
  }
  add("POST", `${API}/anthropic`, ({ body }) => {
    setSecret("ANTHROPIC_API_KEY", keyCheck(body.key, "Anthropic", "console.anthropic.com"));
    mark("anthropic", "done");
    return { ok: true, result: "ok", saved: true, hint: saved.hints.ANTHROPIC_API_KEY, restarting: false,
      message: "Demo: the key looks right, so it's saved for this tab only. The Windows app checks it with Anthropic." };
  });
  add("POST", `${API}/openai`, ({ body }) => {
    setSecret("OPENAI_API_KEY", keyCheck(body.key, "OpenAI", "platform.openai.com"));
    mark("openai", "done");
    return { ok: true, result: "ok", saved: true, hint: saved.hints.OPENAI_API_KEY, restarting: false,
      message: "Demo: the key looks right, so it's saved for this tab only. The Windows app checks it with OpenAI." };
  });
  add("POST", `${API}/safety`, ({ body }) => {
    if (typeof body.computer_use !== "boolean") throw new HTTPError(400, "Computer use is either on or off.");
    saved.settings.JARVIS_COMPUTER_USE = body.computer_use ? "1" : "0";
    mark("safety", "done");
    return { computer_use: body.computer_use, restarting: false, state: view() };
  });

  // the speech model: a simulated 145 MB download
  const download = { state: "idle", bytes: 0, base: 0, total: 145000000, timer: null, error: "" };
  function progress() {
    const percent = download.state === "done" ? 100
      : Math.min(99, Math.floor((100 * Math.max(0, download.bytes - download.base)) / Math.max(1, download.total - download.base)));
    return { state: download.state, model: download.state === "idle" ? "" : "base.en", bytes: download.bytes,
      total: download.total, percent, error: download.error };
  }
  add("GET", `${API}/speech`, () => ({
    microphones: { available: true, devices: MICS, error: "" }, selected: saved.mic, model: "base.en",
    present: saved.model, download: progress(), voice_test: false,
  }));
  add("PUT", `${API}/microphone`, ({ body }) => {
    if (body.name === undefined || body.name === null || body.name === "") {
      saved.mic = "";
      save();
      return { selected: "" };
    }
    if (!MICS.some((mic) => mic.name === body.name)) throw new HTTPError(400, "That microphone isn't plugged in any more. Pick another one.");
    saved.mic = body.name;
    save();
    return { selected: body.name };
  });
  add("GET", `${API}/speech/download`, progress);
  add("POST", `${API}/speech/download`, () => {
    if (download.state === "downloading") throw new HTTPError(409, "The speech model is already downloading.");
    if (download.state === "done" || download.state === "idle") download.bytes = 0;
    Object.assign(download, { state: "downloading", base: download.bytes, error: "" });
    download.timer = setInterval(() => {
      download.bytes = Math.min(download.total, download.bytes + 4200000 + Math.round(Math.random() * 2600000));
      if (download.bytes >= download.total) {
        clearInterval(download.timer);
        download.state = "done";
        saved.model = true;
        save();
      }
    }, 250 / SPEED);
    return progress();
  });
  add("DELETE", `${API}/speech/download`, () => {
    if (download.state === "downloading") {
      clearInterval(download.timer);
      download.state = "cancelled";
    }
    return progress();
  });
  add("POST", `${API}/speech/test`, () => ({ supported: false,
    message: "The voice test runs in the Windows app: it needs your microphone. Nothing in this demo listens." }));

  // Telegram: the shapes are checked; the link code "arrives" from a simulated phone after a few seconds
  add("POST", `${API}/telegram/token`, ({ body }) => {
    const token = clean(body.token);
    if (!token) throw failed("empty", "Paste the bot token from @BotFather first.");
    if (!BOT_TOKEN.test(token)) {
      throw failed("format", "That doesn't look like a bot token. It looks like 123456789:AAE..., and @BotFather sends it when you make a bot with /newbot.");
    }
    setSecret("TELEGRAM_BOT_TOKEN", token);
    return { ok: true, result: "ok", hint: saved.hints.TELEGRAM_BOT_TOKEN, restarting: false,
      message: "Demo: that looks like a bot token, so it's saved for this tab only. The Windows app checks it with Telegram." };
  });
  add("POST", `${API}/telegram/spectrum`, ({ body }) => {
    const secret = clean(body.secret);
    if (!secret) throw failed("empty", "Paste the Photon Spectrum project secret first.");
    if (!SPECTRUM.test(secret)) throw failed("format", "That doesn't look like a project secret (one line, no spaces).");
    setSecret("SPECTRUM_PROJECT_SECRET", secret);
    return { ok: true, result: "ok", message: "Saved the Photon Spectrum secret.", hint: saved.hints.SPECTRUM_PROJECT_SECRET,
      restarting: false };
  });
  function linkStatus() {
    const link = linking || { state: "idle", code: "", deadline: 0, owner: "", wrong: 0 };
    if (link.state === "waiting" && Date.now() > link.deadline) {
      link.state = "expired";
      clearTimeout(link.timer);
    }
    const waiting = link.state === "waiting";
    return { state: link.state, code: waiting ? link.code : "", expires_in: waiting ? Math.max(0, Math.floor((link.deadline - Date.now()) / 1000)) : 0,
      wrong: 0, owner: link.owner, telegram: telegramState(), telegram_error: "", bot: {} };
  }
  add("GET", `${API}/telegram/link`, linkStatus);
  add("POST", `${API}/telegram/link`, () => {
    if (!has("TELEGRAM_BOT_TOKEN") || !has("SPECTRUM_PROJECT_SECRET")) {
      throw new HTTPError(409, "Save the bot token and the Photon Spectrum secret first.");
    }
    if (linking) clearTimeout(linking.timer);
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0");
    linking = { state: "waiting", code, deadline: Date.now() + 600000, owner: "", timer: null };
    const mine = linking;
    mine.timer = setTimeout(() => { // the simulated phone sends the code
      if (linking !== mine || mine.state !== "waiting") return;
      mine.state = "linked";
      mine.owner = "6102938475";
      saved.settings.TELEGRAM_OWNER_ID = mine.owner;
      saved.settings.JARVIS_TELEGRAM = "1";
      mark("telegram", "done");
    }, 7000 / SPEED);
    return linkStatus();
  });
  add("DELETE", `${API}/telegram/link`, () => {
    if (linking && linking.state === "waiting") {
      clearTimeout(linking.timer);
      linking.state = Date.now() > linking.deadline ? "expired" : "cancelled";
    }
    return linkStatus();
  });

  // other PCs
  add("POST", `${API}/pcs/new`, ({ body }) => {
    if (has("JARVIS_TOKEN") && body.replace !== true) {
      throw new HTTPError(409, "This PC already has a pairing code. A new one means your other PCs need the new code too.",
        { result: "exists", hint: saved.hints.JARVIS_TOKEN });
    }
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    const code = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    setSecret("JARVIS_TOKEN", code);
    return { pairing_code: code, grouped: code.match(/.{1,4}/g).join(" "), restarting: false };
  });
  add("POST", `${API}/pcs/join`, ({ body }) => {
    let code = String(body.code || "").normalize("NFKC").replace(/\s+/g, "");
    if (code.toUpperCase().startsWith("JARVIS_TOKEN=")) code = code.slice("JARVIS_TOKEN=".length);
    code = code.replace(/^['"]+|['"]+$/g, "");
    if (!code) throw failed("empty", "Paste the pairing code from your other PC first.");
    if (!/^[\x21-\x7e]+$/.test(code) || code.length > 200) {
      throw failed("format", "That doesn't look like a pairing code. Copy it again from your other PC.");
    }
    if (code.length < 16) {
      throw failed("format", "That code is too short to be safe (at least 16 characters). Make a new one on your first PC.");
    }
    setSecret("JARVIS_TOKEN", code);
    return { ok: true, result: "ok", message: "Saved. This PC finds your other PCs with this code.",
      hint: saved.hints.JARVIS_TOKEN, restarting: false };
  });
  add("PUT", `${API}/pcs`, ({ body }) => {
    const changed = [];
    if ("name" in body) {
      const name = String(body.name || "").trim();
      if (name && !PC_NAME.test(name)) throw failed("format", "Use up to 30 letters, digits, spaces, - and _ for the PC's name.");
      if ((saved.settings.JARVIS_NAME || "") !== name) {
        if (name) saved.settings.JARVIS_NAME = name;
        else delete saved.settings.JARVIS_NAME;
        changed.push("JARVIS_NAME");
      }
    }
    if ("serve" in body) {
      if (typeof body.serve !== "boolean") throw new HTTPError(400, "Sharing this PC is either on or off.");
      if ((saved.settings.JARVIS_SERVE === "1") !== body.serve) {
        saved.settings.JARVIS_SERVE = body.serve ? "1" : "";
        changed.push("JARVIS_SERVE");
      }
    }
    save();
    return { saved: changed.sort(), restarting: false };
  });

  // finish
  function autostart() {
    return { supported: true, enabled: saved.autostart, command: '"C:\\Users\\Alex\\AppData\\Local\\Programs\\Alfred\\Alfred.exe"',
      where: "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", simulated: false };
  }
  add("GET", `${API}/autostart`, autostart);
  add("PUT", `${API}/autostart`, ({ body }) => {
    if (typeof body.on !== "boolean") throw new HTTPError(400, "Starting with Windows is either on or off.");
    saved.autostart = body.on;
    save();
    return autostart();
  });
  add("POST", `${API}/try`, async () => {
    for (let i = 0; i < 40 && !["ready", "busy"].includes(status().engine); i += 1) await sleep(250);
    if (!["ready", "busy"].includes(status().engine)) {
      throw new HTTPError(409, "Alfred isn't ready yet. Wait until it says Ready, then try again.");
    }
    const cmd = submit("open notepad", "desktop");
    await cmd.finished;
    return { command: "open notepad", ok: cmd.ok, replies: cmd.replies, stopped: cmd.stopped, timed_out: false };
  });

  // --- the fetch wrapper -------------------------------------------------------------------------------------------
  function respond(status_, data) {
    return new Response(JSON.stringify(data), { status: status_, headers: { "Content-Type": "application/json; charset=utf-8" } });
  }

  let queue = Promise.resolve();
  async function handle(path, init) {
    const method = String(init.method || "GET").toUpperCase();
    const [pathname, search] = path.split("?");
    const query = new URLSearchParams(search || "");
    if (pathname === "/api/events" && method === "GET") {
      if (init.signal && init.signal.aborted) throw new DOMException("The feed was closed.", "AbortError");
      const after = query.get("after");
      return eventStream(after === null || after === "" || Number.isNaN(Number(after)) ? null : Number(after), init.signal);
    }
    // A local server answers in a few milliseconds, and in the order it was asked.
    const turn = queue.then(() => sleep(20 + Math.random() * 60));
    queue = turn;
    await turn;
    let seenPath = false;
    for (const route of routes) {
      const m = route.pattern.exec(pathname);
      if (!m) continue;
      seenPath = true;
      if (route.method !== method) continue;
      let body = {};
      if (method !== "GET" && typeof init.body === "string" && init.body) {
        try { body = JSON.parse(init.body); } catch { return respond(400, { error: "Body must be JSON." }); }
        if (!body || typeof body !== "object" || Array.isArray(body)) return respond(400, { error: "Body must be a JSON object." });
      }
      try {
        return respond(200, await route.handler({ body, query, params: m.groups || {} }));
      } catch (e) {
        if (e instanceof HTTPError) return respond(e.status, { error: e.message, ...e.extra });
        console.error(e);
        return respond(500, { error: "Alfred had a problem with that. Try again." });
      }
    }
    return seenPath ? respond(405, { error: "Method not allowed." }) : respond(404, { error: "Not found." });
  }

  window.fetch = function fetch(input, init) {
    if (typeof input === "string" && input.startsWith("/api/")) return handle(input, init || {});
    return nativeFetch(input, init);
  };

  // --- the main window opens on a working PC, with an example conversation -------------------------------------------
  function seed() {
    const start_ = Date.now() - 4 * 60000;
    let t = start_;
    const at = (ms) => { t += ms; return t; };
    publish("ready", { after_stop: false, released: 0, version: DATA.version, has_llm: true, computer_use: computerUse(),
      name: pcName(), protocol: 2 }, at(0));
    publish("state", { state: "ready", busy_id: null, queued: 0 }, at(10));
    publish("notice", { text: "Example: a command from earlier on this simulated PC. Your turn is below." }, at(30000));
    const id = hex(12);
    const text = "Open Notepad and write a shopping list";
    const list = "Shopping list\n- Milk\n- Eggs\n- Bread\n- Apples\n- Coffee";
    publish("command", { id, source: "desktop", text }, at(1000));
    publish("state", { state: "busy", busy_id: id, queued: 0 }, at(5));
    publish("status", { id, kind: "thinking", text: "thinking..." }, at(300));
    publish("status", { id, kind: "tool", text: `-> open_app ${pyRepr({ name: "Notepad" })}` }, at(1800));
    writeAudit("open_app", { name: "Notepad" }, true, "Opened Notepad.", at(400));
    publish("status", { id, kind: "tool", text: `-> ui_set_text ${pyRepr({ window: "Notepad", text: list })}` }, at(1500));
    writeAudit("ui_set_text", { window: "Notepad", text: list }, true, 'Set the text of Document "Text editor".', at(300));
    publish("reply", { id, source: "desktop", text: "Done. Notepad is open with your shopping list: milk, eggs, bread, apples and coffee." }, at(1600));
    publish("done", { id, ok: true, source: "desktop", stopped: false }, at(5));
    publish("state", { state: "ready", busy_id: null, queued: 0 }, at(5));
    publish("notice", { text: "That was the example. Now type a command below, or pick one under Try." }, at(20));
    engine.shown = "ready|null|0";
  }

  if (onSetup) {
    engine.shown = "";
    if (saved.consent) publish("ready", { after_stop: false, released: 0, version: DATA.version, has_llm: true,
      computer_use: computerUse(), name: pcName(), protocol: 2 });
    publishState();
  } else {
    seed();
  }

  Demo.server = {
    submit, stop, status, publish, hint, computerUse,
    // "Replay the setup": a new PC in the setup; finishing it (or coming back) restores this one's keys.
    replaySetup() {
      const before = clone({ ...saved, before: null });
      saved = newPC();
      saved.before = before;
      save();
    },
  };
})();
