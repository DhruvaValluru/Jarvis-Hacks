// Alfred's web demo, end to end in headless Chromium. A local tool, not part of CI:
//
//   node demo/tests/e2e.mjs [--shots DIR] [--speed N] [--only main|setup|artifact] [--verbose]
//
// It builds the demo into a temporary folder (python3 demo/build.py --out ...), serves the site under a
// subpath (/Jarvis-Hacks/, as GitHub Pages does) and the artifact copy at /artifact/ with
// python3 -m http.server, then drives them like a judge would: every example button, Allow and Deny,
// an approval left to expire, STOP in the middle of a run (button, and Esc then Enter), two commands
// in a row, Settings, Activity, and the setup replayed to the end and back. Everything runs at 1280x800
// and 400x800, in light and dark. It fails on any console error, failed request, error status, request
// to another site, or a page that scrolls sideways. Screenshots go to --shots (default: a temp folder).
//
// Playwright for Node comes from PLAYWRIGHT_MODULE (default /opt/node22/lib/node_modules/playwright/index.mjs);
// it never installs browsers. PYTHON picks the Python (default python3, or python on Windows).

import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PYTHON = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const MODULE = process.env.PLAYWRIGHT_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";

const args = { shots: join(tmpdir(), "alfred-demo-shots"), speed: 3, only: "", verbose: false };
for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (arg === "--shots") args.shots = resolve(process.argv[++i]);
  else if (arg === "--speed") args.speed = Number(process.argv[++i]);
  else if (arg === "--only") args.only = process.argv[++i];
  else if (arg === "--verbose") args.verbose = true;
  else {
    console.error(`Unknown option ${arg}. Use --shots DIR, --speed N, --only main|setup|artifact, --verbose.`);
    process.exit(2);
  }
}

const { chromium } = await import(MODULE);
const failures = [];
let label = "";

function check(ok, what) {
  if (!ok) {
    failures.push(`${label}: ${what}`);
    console.log(`  FAIL ${what}`);
  } else if (args.verbose) console.log(`  ok   ${what}`);
  return ok;
}

// --- build and serve ---------------------------------------------------------------------------------------
const work = mkdtempSync(join(tmpdir(), "alfred-demo-e2e-"));
const built = spawnSync(PYTHON, [join(ROOT, "demo", "build.py"), "--out", join(work, "dist")], { encoding: "utf8" });
if (built.status !== 0) {
  console.error(built.stdout, built.stderr);
  process.exit(1);
}
const served = join(work, "serve");
cpSync(join(work, "dist", "site"), join(served, "Jarvis-Hacks"), { recursive: true });
cpSync(join(work, "dist", "artifact"), join(served, "artifact"), { recursive: true });
// The artifact's host wraps its main page in a document; do the same, so it isn't shown in quirks mode.
const main = readFileSync(join(served, "artifact", "index.html"), "utf8");
writeFileSync(join(served, "artifact", "index.html"), "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
  + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n</head>\n<body>\n" + main + "</body>\n</html>\n");
mkdirSync(args.shots, { recursive: true });

const port = await new Promise((done) => {
  const probe = net.createServer().listen(0, "127.0.0.1", () => {
    const { port: free } = probe.address();
    probe.close(() => done(free));
  });
});
const server = spawn(PYTHON, ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", served], { stdio: "ignore" });
const ORIGIN = `http://127.0.0.1:${port}`;
for (let i = 0; i < 100; i += 1) {
  const up = await new Promise((done) => {
    const socket = net.connect(port, "127.0.0.1", () => { socket.end(); done(true); }).on("error", () => done(false));
  });
  if (up) break;
  await new Promise((done) => { setTimeout(done, 100); });
}

// --- helpers ------------------------------------------------------------------------------------------------------
async function open(browser, { width, height = 800, scheme = "light", theme = "", test = {}, motion = "no-preference" }) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, reducedMotion: motion });
  const page = await context.newPage();
  await page.addInitScript(({ speed, extra, chosen }) => {
    window.AlfredDemoTest = { speed, ...extra };
    if (!chosen || !/\/artifact\/(index\.html)?$/.test(location.pathname)) return; // the host themes its main page only
    const apply = () => document.documentElement && document.documentElement.setAttribute("data-theme", chosen);
    if (document.documentElement) apply();
    else new MutationObserver((_, watcher) => { if (document.documentElement) { apply(); watcher.disconnect(); } }).observe(document, { childList: true });
  }, { speed: args.speed, extra: test, chosen: theme });
  page.on("console", (message) => {
    if (message.type() === "error") check(false, `console error: ${message.text()}`);
    else if (message.type() === "warning" && args.verbose) console.log(`  warning: ${message.text()}`);
  });
  page.on("pageerror", (error) => check(false, `page error: ${error.message}`));
  page.on("requestfailed", (request) => check(false, `failed request: ${request.url()} (${request.failure()?.errorText})`));
  page.on("response", (response) => { if (response.status() >= 400) check(false, `HTTP ${response.status()} for ${response.url()}`); });
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(ORIGIN) && !url.startsWith("data:") && !url.startsWith("blob:")) check(false, `request to another site: ${url}`);
  });
  return { context, page };
}

const shotName = (name) => `${label.replace(/[^a-z0-9]+/gi, "-")}-${name}.png`.toLowerCase();
async function shot(page, name) {
  await page.screenshot({ path: join(args.shots, shotName(name)) });
}

async function noSideScroll(page, where) {
  const [scroll, client] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  check(scroll <= client, `${where} doesn't scroll sideways (content ${scroll} px, window ${client} px)`);
}

const replyCount = (page) => page.locator("#log .entry.alfred").count();

async function reply(page, before, pattern, timeout = 30000) {
  try {
    await page.waitForFunction((n) => document.querySelectorAll("#log .entry.alfred").length > n, before, { timeout });
  } catch {
    check(false, `no reply matching ${pattern}`);
    return "";
  }
  const text = (await page.locator("#log .entry.alfred .text").nth(before).textContent()) || "";
  check(pattern.test(text), `reply ${pattern} (got: ${text.slice(0, 140)})`);
  return text;
}

async function line(page, text, times = 1, timeout = 20000) {
  try {
    await page.waitForFunction(([wanted, n]) => [...document.querySelectorAll("#log .entry")]
      .filter((entry) => entry.textContent.includes(wanted)).length >= n, [text, times], { timeout });
    return check(true, `log says "${text}"`);
  } catch {
    return check(false, `log never said "${text}"${times > 1 ? ` ${times} times` : ""}`);
  }
}

async function chip(page, name) {
  await page.locator(".demo-chip", { hasText: name }).click();
}

// Type a command and send it; Alfred has taken it once the box is empty again (it may still be busy).
async function type(page, text) {
  await page.fill("#input", text);
  await page.press("#input", "Enter");
  await page.waitForFunction(() => document.getElementById("input").value === "", null, { timeout: 10000 });
}

async function approval(page) {
  try {
    await page.locator("#approval").waitFor({ state: "visible", timeout: 30000 });
  } catch {
    check(false, "the approval dialog never opened");
    return "";
  }
  return (await page.locator("#approval-reason").textContent()) || "";
}

async function steps(page, n) {
  const start = await page.locator("#log .entry.step").count();
  await page.waitForFunction(([s, k]) => document.querySelectorAll("#log .entry.step").length >= s + k, [start, n], { timeout: 20000 });
}

async function ready(page) {
  await page.locator('#pill[data-state="ready"]').waitFor({ timeout: 20000 });
}

// --- the main window --------------------------------------------------------------------------------------------
async function mainWindow(browser, view) {
  const { context, page } = await open(browser, view);
  await page.goto(`${ORIGIN}/Jarvis-Hacks/`);
  await ready(page);
  const log = await page.locator("#log").innerText();
  check(log.includes("Open Notepad and write a shopping list") && log.includes("Example"), "opens with the example conversation");
  check(await page.locator(".demo-bar").getByText("simulated PC").count() > 0, "the banner says it's a simulated PC");
  check(await page.locator("#demo-pc .demo-pc__title").textContent() === "Simulated PC", "the panel is titled Simulated PC");
  check(await page.locator('a[href="https://github.com/DhruvaValluru/Jarvis-Hacks/releases/latest"]').count() === 1, "links the Windows download");
  await noSideScroll(page, "the main window");
  await shot(page, "1-main");

  let n = await replyCount(page);
  await chip(page, "Type a note");
  await reply(page, n, /^Done\. Notepad is open and says: “Call Sam about the report at 3 pm”$/);
  check((await page.locator("#demo-desktop .np-text").textContent()).includes("Call Sam about the report"), "Notepad on the simulated PC has the note");

  n = await replyCount(page);
  await chip(page, "Play music");
  await reply(page, n, /^Playing “Blue Skies” by The Aero Band, and the volume is at 30%\.$/);

  n = await replyCount(page);
  await chip(page, "How's my PC");
  await reply(page, n, /^Your PC is doing fine\. CPU is at \d+% and memory at \d+%\. The busiest app is Microsoft Edge/);

  n = await replyCount(page);
  await chip(page, "What's on screen");
  await reply(page, n, /^Besides my window, you have \d windows open\. In front is Task Manager\./);
  await shot(page, "2-screen");

  n = await replyCount(page);
  await chip(page, "Email Sam");
  let reason = await approval(page);
  check(reason === 'click "Send" in Quarterly report - Message', `the email approval asks about Send (got: ${reason})`);
  check(/\d+ s to answer/.test(await page.locator("#approval-time").textContent()), "the approval counts down");
  await shot(page, "3-approval");
  await page.click("#allow");
  await reply(page, n, /^Sent\. Sam has the quarterly report/);
  await line(page, "Allowed (answered by this window).");

  n = await replyCount(page);
  await type(page, "email sam the quarterly report again");
  await approval(page);
  await page.click("#deny");
  await reply(page, n, /^Okay, I didn't send it\.$/);
  await line(page, "Not allowed (answered by this window).");

  n = await replyCount(page);
  await chip(page, "Copy it to my laptop");
  reason = await approval(page);
  check(reason.startsWith('copy_file({"from_pc": "Desktop", "from_path": "C:\\\\Users\\\\Alex\\\\Documents\\\\Quarterly report.pdf"'),
    `the copy asks with copy_file's arguments (got: ${reason.slice(0, 80)})`);
  await page.click("#allow");
  try {
    await page.locator("#demo-laptop .lp-ask").waitFor({ state: "visible", timeout: 10000 });
    check((await page.locator("#demo-laptop .lp-reason").textContent()).includes("asked by Desktop, another PC"), "the laptop asks its own user");
    await shot(page, "4-laptop");
  } catch {
    check(false, "the laptop never asked");
  }
  await reply(page, n, /^Done\. Quarterly report\.pdf is in the Downloads folder on your laptop\./);
  check((await page.locator("#demo-laptop .fx-list").textContent()).includes("Quarterly report.pdf"), "the file is on the laptop");

  n = await replyCount(page);
  await chip(page, "Fill in a form");
  reason = await approval(page);
  check(reason === 'click Button "Submit" on screen', `computer use asks before Submit (got: ${reason})`);
  check(await page.locator("#demo-desktop .dk-pointer.is-shown").count() === 1, "the pointer shows on the simulated PC");
  await shot(page, "5-computer-use");
  await page.click("#allow");
  await reply(page, n, /^Done\. I filled in your name and email and pressed Submit\./);
  check(/left_click \{'coordinate': \[\d+, \d+\]\}/.test(await page.locator("#log").innerText()), "the log shows the clicks");

  n = await replyCount(page);
  await chip(page, "Remind me");
  await reply(page, n, /^Okay, I'll remind you to stretch in 10 seconds\.$/);
  try {
    await page.locator("#log .entry.announce", { hasText: "Reminder: Time to stretch." }).waitFor({ timeout: 20000 });
    check(true, "the reminder came");
  } catch {
    check(false, "the reminder never came");
  }

  n = await replyCount(page);
  await type(page, "order me a pizza");
  await reply(page, n, /^In this web demo I only run a few example tasks on a simulated PC/);

  n = await replyCount(page);
  await type(page, "volume 20");
  await reply(page, n, /^Volume set to 20%\.$/);

  // two in a row: the second waits for the first, like the real engine
  n = await replyCount(page);
  await type(page, "What's on my screen?");
  await type(page, "next song");
  await reply(page, n, /^Besides my window/);
  await reply(page, n + 1, /^next done\.$/);

  // STOP in the middle of a run, then a new command
  n = await replyCount(page);
  await chip(page, "Email Sam");
  await steps(page, 3);
  await page.click("#stop");
  await line(page, "Stopped from this window. Alfred let go of the mouse and keyboard and is starting again.");
  await line(page, "Ready again.");
  check(await page.locator("#approval").isHidden(), "no approval is left open after Stop");
  await ready(page);
  check(await replyCount(page) === n, "the stopped command never replied");
  await type(page, "what time is it");
  await reply(page, n, /^It's \w+ \d\d \w+ \d{4}, \d\d:\d\d (AM|PM)\. CPU \d+%, memory \d+% used\.$/);

  // Esc goes to STOP, Enter presses it, even while an approval waits
  n = await replyCount(page);
  await chip(page, "Fill in a form");
  await approval(page);
  await page.keyboard.press("Escape");
  check(await page.evaluate(() => document.activeElement && document.activeElement.id) === "stop", "Esc moves to STOP");
  await page.keyboard.press("Enter");
  await line(page, "Stopped from this window.", 2);
  await line(page, "Ready again.", 2);
  await line(page, "Not allowed (answered by Stop).");
  await ready(page);
  n = await replyCount(page);
  await type(page, "open notepad");
  await reply(page, n, /^Opened Notepad\.$/);

  // the Windows app's Stop hotkey works here too
  await chip(page, "How's my PC");
  await steps(page, 2);
  await page.keyboard.press("Control+Alt+Backspace");
  await line(page, "Stopped from the Stop hotkey.");
  await line(page, "Ready again.", 3);
  await ready(page);

  // voice mode is the Windows app's
  await page.click("#talk", { force: true }); // aria-disabled, but it still answers a press
  await line(page, "Voice mode runs in the Windows app (it needs your microphone and an OpenAI key).");
  await shot(page, "6-conversation");

  // Settings
  await page.click('.tab[data-view="settings"]');
  await page.locator("#view-settings").waitFor({ state: "visible" });
  check(await page.locator("#view-settings .demo-note").isVisible(), "Settings says nothing leaves the page");
  try {
    await page.locator("#settings-file", { hasText: "(simulated: kept in this page)" }).waitFor({ timeout: 10000 });
    check(true, "the settings file is marked simulated");
  } catch {
    check(false, "the settings file is marked simulated");
  }
  await page.selectOption('select[name="JARVIS_EFFORT"]', "high");
  await page.click('#settings-form button[type="submit"]');
  await page.locator("#settings-msg", { hasText: "Saved. Alfred restarts to use it" }).waitFor({ timeout: 10000 });
  await ready(page);
  await page.fill('input[data-key="OPENAI_API_KEY"]', "sk-proj-abcdefghijklmnopqrstuvwxyz1234");
  await page.locator(".secret", { has: page.locator('input[data-key="OPENAI_API_KEY"]') }).locator("button.primary").click();
  await page.locator(".secret .state.set", { hasText: "Saved: sk-proj-...1234" }).waitFor({ timeout: 10000 });
  check(!(await page.locator("#secrets").innerHTML()).includes("abcdefghijklmnop"), "a saved key is never shown again");
  await page.click("#telegram-toggle");
  await page.locator("#telegram-state", { hasText: "Telegram: only in the Windows app" }).waitFor({ timeout: 10000 });
  check(await page.locator("#setup-steps button").count() === 10, "the Setup card lists the 10 steps");
  await noSideScroll(page, "Settings");
  await shot(page, "7-settings");

  // Activity
  await page.click('.tab[data-view="activity"]');
  await page.locator("#audit-rows tr").first().waitFor({ timeout: 10000 });
  const rows = await page.locator("#audit-rows tr").count();
  check(rows >= 20, `the activity log has the tool calls (${rows} rows)`);
  check(await page.locator("#audit-rows tr", { hasText: "declined by user" }).count() >= 1, "a denied approval shows as not done");
  await page.fill("#audit-filter", "copy_file");
  check(await page.locator("#audit-rows tr").count() >= 1, "the filter finds copy_file");
  await page.fill("#audit-filter", "");
  await shot(page, "8-activity");

  // the setup, replayed to the end and back
  await page.click('.tab[data-view="home"]');
  await page.click("#demo-replay");
  await page.waitForURL(/\/Jarvis-Hacks\/onboarding\/index\.html/);
  await setup(page);
  await page.waitForURL(/\/Jarvis-Hacks\/index\.html$/);
  await ready(page);
  check((await page.locator("#log").innerText()).includes("That was the example"), "back on the main window after the setup");
  n = await replyCount(page);
  await type(page, "what's open");
  await reply(page, n, /^Open windows \(\* = active\):\n\* Alfred {2}\[Alfred\.exe\]/);
  await context.close();
}

// --- the setup -------------------------------------------------------------------------------------------------------
async function setup(page) {
  const next = page.locator("#next");
  const step = (name) => page.locator(`.step[data-step="${name}"]`).waitFor({ state: "visible", timeout: 10000 });
  await step("welcome");
  await page.locator("#checks .check-text").first().waitFor();
  check(await page.locator(".demo-bar--setup").isVisible(), "the setup has the demo banner");
  await noSideScroll(page, "the setup");
  await shot(page, "9-setup-welcome");
  await next.click();
  await step("consent");
  await page.locator("#notice h2").first().waitFor();
  check((await page.locator("#notice").textContent()).includes("Where your data goes"), "the data notice is the real one");
  await next.click();
  await step("anthropic");
  check(await next.isDisabled(), "Next waits for a key");
  await page.fill("#anthropic-key", "not a key");
  await page.click("#anthropic-check");
  await page.locator("#anthropic-msg", { hasText: "That doesn't look like an API key." }).waitFor();
  await page.fill("#anthropic-key", `sk-ant-api03-${"x".repeat(30)}`);
  await page.click("#anthropic-check");
  await page.locator("#anthropic-msg", { hasText: "Demo: the key looks right" }).waitFor();
  await next.click();
  await step("safety");
  await next.click();
  await step("speech");
  await page.click("#download");
  await page.locator("#download-text", { hasText: "Downloaded." }).waitFor({ timeout: 30000 });
  await page.click("#voice-test");
  await page.locator("#voice-result", { hasText: "runs in the Windows app" }).waitFor();
  await shot(page, "10-setup-voice");
  await next.click();
  await step("openai");
  await page.click("#skip");
  await step("telegram");
  await page.fill("#bot-token", `123456789:AAE${"a".repeat(32)}`);
  await page.click("#bot-check");
  await page.locator("#bot-msg", { hasText: "Demo: that looks like a bot token" }).waitFor();
  await page.fill("#spectrum-secret", "spectrum-demo-secret");
  await page.click("#spectrum-save");
  await page.locator("#spectrum-msg", { hasText: "Saved the Photon Spectrum secret." }).waitFor();
  await page.click("#link-start");
  await page.locator("#link-code").waitFor({ state: "visible" });
  check(/^\d{3} \d{3}$/.test(await page.locator("#link-code").textContent()), "a six-digit code shows");
  await shot(page, "11-setup-telegram");
  await page.locator("#link-msg", { hasText: "Linked:" }).waitFor({ timeout: 30000 });
  await page.waitForFunction(() => !document.getElementById("next").disabled, null, { timeout: 10000 });
  await next.click();
  await step("pcs");
  await page.click("#pair-new");
  await page.locator("#pair-box").waitFor({ state: "visible" });
  check((await page.locator("#pair-shown").textContent()).split(" ").length === 8, "a grouped pairing code shows");
  await next.click();
  await step("finish");
  await page.locator("#summary .check").first().waitFor();
  check((await page.locator("#summary").textContent()).includes("Connected."), "the summary has Claude connected");
  await shot(page, "12-setup-finish");
  await next.click();
}

// An approval nobody answers: "nobody in time", then the same answer as a No.
async function expiry(browser, view) {
  const { context, page } = await open(browser, { ...view, test: { approvalTimeout: 2 } });
  await page.goto(`${ORIGIN}/Jarvis-Hacks/index.html`);
  await ready(page);
  const n = await replyCount(page);
  await chip(page, "Email Sam");
  await approval(page);
  await line(page, "Not allowed (answered by nobody in time).");
  await reply(page, n, /^Okay, I didn't send it\.$/);
  check(await page.locator("#approval").isHidden(), "the expired approval closed");
  await context.close();
}

// Leaving a replayed setup half way: the main window opens on the set-up PC again, not the setup.
async function leaveSetup(browser, view) {
  const { context, page } = await open(browser, view);
  await page.goto(`${ORIGIN}/Jarvis-Hacks/`);
  await ready(page);
  await page.click("#demo-replay");
  await page.locator('.step[data-step="welcome"]').waitFor({ state: "visible" });
  await page.click("#demo-back");
  await page.waitForURL(/\/Jarvis-Hacks\/index\.html$/);
  await ready(page);
  check(page.url().endsWith("/Jarvis-Hacks/index.html"), "Back to the demo returns to the main window");
  await page.click('.tab[data-view="settings"]');
  await page.locator(".secret .state.set", { hasText: "sk-ant-...4f2c" }).waitFor({ timeout: 10000 });
  check(true, "the set-up PC's key is back");
  await context.close();
}

// The artifact copy: the host's data-theme wins over the system, the demo works there, and so does the setup.
async function artifact(browser, view) {
  const { context, page } = await open(browser, view);
  await page.goto(`${ORIGIN}/artifact/index.html`);
  await ready(page);
  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return [style.getPropertyValue("--aero-text").trim(), style.getPropertyValue("--win-bg").trim(), getComputedStyle(document.body).backgroundColor];
  });
  const dark = view.theme === "dark";
  check(tokens[0] === (dark ? "#e8f5ff" : "#0b2540"), `data-theme="${view.theme}" picks the theme (--aero-text ${tokens[0]})`);
  check(tokens[1] === (dark ? "#202020" : "#f3f3f3"), `the simulated PC follows it too (--win-bg ${tokens[1]})`);
  check(tokens[2] !== "rgba(0, 0, 0, 0)", `the body has a background (${tokens[2]})`);
  await shot(page, "artifact");
  const n = await replyCount(page);
  await chip(page, "What's on screen");
  await reply(page, n, /^Besides my window/);
  await page.click("#demo-replay");
  await page.waitForURL(/\/artifact\/onboarding\/index\.html/);
  await page.locator('.step[data-step="welcome"]').waitFor({ state: "visible" });
  const setupText = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--aero-text").trim());
  check(setupText === (dark ? "#e8f5ff" : "#0b2540"), `the setup keeps the host's theme (${setupText})`);
  await shot(page, "artifact-setup");
  await page.click("#demo-back");
  await page.waitForURL(/\/artifact\/index\.html$/);
  await ready(page);
  await context.close();
}

// --- run ---------------------------------------------------------------------------------------------------------------
const VIEWS = [
  { width: 1280, height: 800, scheme: "light" },
  { width: 400, height: 800, scheme: "dark" },
  { width: 1280, height: 800, scheme: "dark" },
  { width: 400, height: 800, scheme: "light", motion: "reduce" },
];
const browser = await chromium.launch();
try {
  for (const view of VIEWS) {
    label = `${view.width}x${view.height}-${view.scheme}${view.motion === "reduce" ? "-reduced-motion" : ""}`;
    if (!args.only || args.only === "main") {
      console.log(`main window and setup, ${label}`);
      await mainWindow(browser, view);
    }
  }
  label = "1280x800-light";
  if (!args.only || args.only === "setup") {
    console.log("an approval left to expire; leaving the setup half way");
    await expiry(browser, VIEWS[0]);
    await leaveSetup(browser, VIEWS[1]);
  }
  if (!args.only || args.only === "artifact") {
    for (const [view, theme] of [[VIEWS[0], "dark"], [VIEWS[1], "light"]]) {
      label = `artifact-${view.width}-${view.scheme}-system-${theme}-chosen`;
      console.log(`artifact, ${label}`);
      await artifact(browser, { ...view, theme });
    }
  }
} catch (error) {
  failures.push(`${label}: ${error.message.split("\n")[0]}`);
  console.error(error);
} finally {
  await browser.close();
  server.kill();
  rmSync(work, { recursive: true, force: true });
}

console.log(`\nScreenshots: ${args.shots}`);
if (failures.length) {
  console.log(`\n${failures.length} problem(s):\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log("All good: no console errors, no failed or outside requests.");
