# Jarvis master task list

**Every agent and every person reads this file before starting work.** It lists every step to build Jarvis, split by area, in order. Who is working on what right now is in the auto-generated board, [STATUS.md](STATUS.md), rebuilt from open PRs and branches every hour and on every PR change.

## How to use this list (agents: follow exactly)

1. **Sync first.** `git fetch origin` and read `origin/main:docs/TASKS.md` and `origin/main:docs/STATUS.md`. Your branch's copy may be stale.
2. **Pick a task.** Choose the **lowest-numbered open task in your area** whose `needs:` are done (`[x]`) and which STATUS.md doesn't show as claimed. If your person gave you a specific task, take that one. Area 0 (integration) beats everything else.
3. **Claim it.** Put the task ID in your **branch name or PR title**, e.g. `[C6] Per-turn effort for computer use`. Open a **draft PR early**: that is the claim, and the board picks it up within an hour. If you can't open a PR, put the ID in your commit messages.
4. **Stay in scope.** One task (or a few adjacent ones) per PR. Respect the file-ownership rules below.
5. **Test** per [windows-mvp/TESTING.md](../windows-mvp/TESTING.md): unit tests locally, CI green on Windows.
6. **Update this file in the same PR:** tick `[x]` on what you finished, add the PR number, and **append any new tasks you discovered** at the end of the right area with the next free ID. Never renumber or delete IDs; mark dropped ones `[-]` with a reason.
7. **Merge `main` into your branch at least once a day** and before asking for review.

Legend: `[ ]` open · `[x]` done · `[-]` dropped · `needs:` must be done first · *done when* is the acceptance test.

## File ownership (to avoid merge conflicts)

| Files | Owner area | Rule for others |
|---|---|---|
| `jarvis/brain.py`, `jarvis/__main__.py`, `jarvis/config.py` | A (core) | Hot spots: add small hooks or flags, don't restructure. Merge `main` before editing. |
| `jarvis/tools/uia.py`, `apps.py`, `office.py`, `system.py` | B (tools) | Add new tools in new files when you can. |
| `jarvis/tools/computer.py`, `wininput.py` | C (computer use) | |
| `jarvis/voice.py` | D (voice) | |
| `windows-mvp/telegram/`, `jarvis/telegram_worker.py` | E (remote) | |
| `requirements*.txt`, `requirements.lock.txt`, `test.bat`, `setup.bat`, `.github/workflows/`, `jarvis/doctor.py` | G (testing spec) | **Spec changes need their own PR** and a `SPEC_VERSION` bump (TESTING.md). |
| `docs/TASKS.md` | everyone | Only tick your own tasks and append new ones. |
| `docs/STATUS.md` | the bot | Never edit by hand; it is overwritten. |

---

## 0. Integration (do these first; they unblock everyone)

- [x] **0.1** Merge PR #5 (shared test spec) into `main`. *Done when:* `main` has `test.bat`, `requirements.lock.txt`, and CI `spec-test` green.
- [x] **0.2** Merge PR #4 (computer use) after merging `main` into `eshaan-work`. needs: 0.1. *Done when:* `test.bat` passes in CI on the merged head. Resolve `tests/test_core.py` and `__main__.py` conflicts by keeping both sides.
- [x] **0.3** Add `JARVIS_COMPUTER_USE` and `JARVIS_MAX_STEPS` to the test profile (`doctor.TEST_PROFILE` and `test.bat`), bump `SPEC_VERSION` to 2. needs: 0.2.
- [x] **0.4** Open a PR for `Saatvik-Branch` (Telegram). Before it merges: (a) merge `main`; (b) move its CI steps into the new `spec-test` job (Node 22, `npm ci`, `npm test`) instead of the old job; (c) add Node 22 and the `package-lock.json` to the spec in TESTING.md and the doctor (spec bump); (d) add `tests/test_telegram.py` to `test.bat`. needs: 0.1.
- [x] **0.5** Unify the reply and confirm hooks: Telegram adds `on_reply`/`on_confirm` to `Jarvis`; computer use adds its own confirms. Make every confirmation go through `Jarvis.confirm`, so a Telegram user gets the same y/N for Send/Delete and computer-use clicks. needs: 0.2, 0.4.
- [ ] **0.7** PR #6 (browser voice mode on OpenAI's live voice model, `jarvisvoice`): decide how it relates to area D (local voice) and E3 (voice service). Then give it a task ID, merge `main`, and add its tests to `test.bat`, or keep it as a separate app.
- [ ] **0.8** PR #7 (multi-PC control, peer-to-peer tool calls): reconcile with the hub/worker design in `docs/ARCHITECTURE.md` section 3.6 (tasks I1–I6) before merging. Decide: peer-to-peer now and the hub later, or the hub only. Then give it a task ID and merge `main`.
- [ ] **0.9** The assistant's name is **Alfred**. *Done:* what users see (Claude's self-name, console text, Telegram, data notice, MVP README; the wake word accepts "Alfred" and "Jarvis"). *Open:* decide whether to also rename code, scripts and the repo (`jarvis.bat`, `python -m jarvis`); that breaks every open branch, so do it only at a quiet moment.
- [ ] **0.6** Delete the stale branches `mvp` and `claude/sharp-bohr-sglils` (already merged). Ask the repo owner first.

## A. Core brain (Claude loop, routing, memory)

- [x] **A1** Claude tool-use loop with refusal fallback, confirmation gates and an audit log. (main)
- [x] **A2** Fast-path router for common commands without an API key. (main)
- [ ] **A3** Stream Claude's reply and speak the first sentence before the whole reply is done. *Done when:* time to first audio is under 1.5 s on a simple question (measure and log it).
- [ ] **A4** Per-turn effort: `low` for chat, `medium` when the turn uses computer use or more than 3 tools. needs: 0.2.
- [ ] **A5** Prompt caching check: log `cache_read_input_tokens`; fix anything that breaks the cache (tools order, system text). *Done when:* the second request in a session shows cache reads > 0.
- [ ] **A6** Session memory: persist a short summary of each conversation to `%LOCALAPPDATA%\Jarvis\memory.jsonl`, and load the last N into context on start. *Done when:* "what did I ask you yesterday" works.
- [ ] **A7** "Stop" or "cancel" mid-task aborts the current tool loop (voice, Telegram and Ctrl+C).
- [ ] **A8** Router coverage: log every request that went to Claude, find the 10 most common simple ones, and add them to the router with tests.

## B. Windows tools (UI Automation, COM, apps)

- [x] **B1** Apps (Start menu launch, list/focus/close windows), UIA list/click/set/read, send_keys, Excel/Word COM, media, volume, clipboard, timers, PowerShell with confirmation. (main)
- [ ] **B2** Outlook COM: read the latest emails and draft (never send without confirmation). *Done when:* "summarize my last 5 emails" works with Outlook open.
- [ ] **B3** PowerPoint COM: add a slide with a title and bullets.
- [ ] **B4** Browser control through UIA for Edge and Chrome: read the current tab's URL and title, open a link in a new tab. *Done when:* "what page am I on" works.
- [ ] **B5** File tools: find files by name or date in user folders, open, move or rename with confirmation for deletes. *Done when:* "rename my screenshots from today" works.
- [ ] **B6** Settings tools: Wi-Fi on/off, Bluetooth, Do Not Disturb, dark mode, display brightness. Prefer `ms-settings:` URIs, WMI or PowerShell over clicking.
- [ ] **B7** Spotify: play a named playlist reliably (URI and UIA search box, or the Spotify Web API with OAuth). *Done when:* "play my focus playlist" works 5 times out of 5.
- [ ] **B8** Elevated apps: document and test running Jarvis elevated, or with UI access, so it can control admin windows.
- [ ] **B9** UIA performance: cache the control tree per window and cap walks at 2 s. *Done when:* `ui_list_controls` on Word takes under 2 s.

## C. Computer use (vision fallback) — merged from `eshaan-work`

- [x] **C1** Input and capture: DPI awareness, 1280 px screenshots, coordinate mapping, SendInput. (merged)
- [x] **C2** Brain wiring for `computer_toolset_20260801`: halt-on-failure, `toolset_name` echo. (merged)
- [x] **C3** Safety: risky-click confirm, password-field refusal, lock screen/UAC stop, corner kill switch. (merged)
- [x] **C4** Offline tests and a Windows Notepad click-and-type smoke test. (merged)
- [ ] **C5** Manual end-to-end run with a real key: Paint "draw a square", one browser task, one game or launcher. Record results in the PR. needs: 0.2.
- [ ] **C6** Confirm before pressing Enter in chat and email apps (Slack, Teams, Discord, Outlook, WhatsApp).
- [ ] **C7** Measure cost and latency per step; compare 1024 vs 1280 px. Write the numbers in `docs/COMPUTER_USE_PLAN.md`.
- [ ] **C8** Multi-monitor: capture the monitor the target window is on.
- [ ] **C9** "Jarvis is driving" overlay with a Stop button, plus a global stop hotkey.
- [x] **C10** Remote stop for computer use: the corner kill switch only works for someone at the PC. When a task comes from Telegram, `/stop` must abort computer use mid-turn. needs: E2. *Decision:* `/stop` is a hard stop (kill the worker tree, restart, release held keys and buttons).

## D. Voice (local)

- [x] **D1** Push-to-talk and "Jarvis, ..." wake mode, local Whisper, Windows voice output. (main)
- [x] **D2** Offline voice test in CI (SAPI to WAV to Whisper to router). (PR #5)
- [ ] **D3** Better end-of-speech detection with Silero VAD instead of the loudness threshold. *Done when:* no cut-offs in 10 test phrases recorded in a noisy room.
- [ ] **D4** Real wake word (openWakeWord "hey jarvis") instead of transcribing everything. *Done when:* CPU use while idle is under 5%.
- [ ] **D5** Barge-in: stop speaking when the user starts talking.
- [ ] **D6** Natural voice option. Pick one TTS service for the whole project (shared with E3) and add it behind `JARVIS_TTS=sapi|<service>`.
- [ ] **D7** Global push-to-talk hotkey that works when the console isn't focused.

## E. Remote control (Telegram, phone) — merged from `Saatvik-Branch`

- [x] **E1** Telegram bridge through Photon Spectrum, owner-only, text and voice notes, OpenAI transcription and spoken replies. (merged from `Saatvik-Branch`)
- [x] **E2** Integration work in 0.4 and 0.5. (Telegram tests in `test.bat` and CI; approvals go through `Jarvis.confirm`, so computer-use and Send clicks ask in Telegram.)
- [ ] **E3** Voice service from `windows-mvp/VOICE_API.md` (`POST /speech`, `POST /transcribe`). *Current state:* the Telegram bridge calls OpenAI directly for voice notes and spoken replies; the custom service is optional. Decide whether to keep OpenAI or build the service, and record it here.
- [ ] **E4** Confirmations over Telegram: inline Yes/No buttons, with a 60 s timeout that defaults to No. needs: 0.5.
- [ ] **E5** Send screenshots to Telegram on request ("show me my screen").
- [x] **E7** Remote computer-use guardrails: decide whether computer use is allowed from Telegram by default (screenshots of the PC leave the house, and nobody is at the PC to stop the mouse). Add `JARVIS_REMOTE_COMPUTER_USE` if needed. *Decision:* yes, Telegram is how you text Alfred, so computer use stays on from Telegram; risky clicks ask in Telegram and `/stop` (C10) stops it.
- [ ] **E6** Security review of the remote path: owner check, replay of old messages, what happens if the bot token leaks, rate limits. Write findings in `docs/CREDENTIALS_AND_USER_SECURITY.md`.

## F. Safety and security (applies to every area)

- [x] **F1** Confirm tier for PowerShell and risky clicks; text from apps is data; audit log. (main)
- [ ] **F2** Prompt-injection test suite: documents and pages telling Jarvis to "click Send" or "type your password". *Done when:* every case refuses or asks, and the suite runs in `test.bat`.
- [ ] **F3** Kill switch that stops all agents and remote sessions at once (hotkey, tray, Telegram `/stop`).
- [ ] **F4** Per-app permissions (allow, ask, never) for UIA and computer use.
- [ ] **F5** Audit log viewer: a local page listing actions with filters.
- [ ] **F6** User-approved credential fill (plan in `docs/CREDENTIALS_AND_USER_SECURITY.md`). Not before F2.
- [x] **F8** Third-party data map: list what goes to Anthropic (prompts, screenshots), OpenAI (voice notes, reply audio) and Photon (Telegram messages), and show it to users in the README. *Done:* `windows-mvp/PRIVACY.md`, accepted at setup (`jarvis --terms`, versioned), required before the console or Telegram runs, shown on Telegram `/start` and `/privacy`.
- [ ] **F9** `npm ci` in `windows-mvp/telegram` reports 18 moderate vulnerabilities. Run `npm audit`, update what's safe, and note the rest. This changes `package-lock.json`, so it's a spec change.
- [ ] **F7** Secrets hygiene: no keys in Git (scan in CI), `.env` only, and document key rotation.

## G. Testing spec and CI

- [x] **G1** Shared spec: lock file, doctor fingerprint, `test.bat`, Sandbox runner, pinned CI. (PR #5)
- [x] **G2** Live task board generated from PRs and branches ([STATUS.md](STATUS.md)).
- [ ] **G3** Claude scenario tests with a real key, run nightly in CI with a repo secret: the manual scenarios M1, M7 and M8 from TESTING.md, scripted. *Done when:* a nightly job reports pass/fail.
- [ ] **G4** Make the `spec-test` CI check required on `main` (branch protection; repo owner).
- [ ] **G5** Self-hosted Windows 11 runner (a team PC) for tests that need audio, Office or a real desktop.

## H. Phase 1: background service and system events

- [ ] **H1** Design doc: what the service does vs the user-session agent, and how they talk (named pipe with an ACL). Follow README section 4.
- [ ] **H2** Service skeleton (C#/.NET 8 or Python), with install and uninstall scripts that need admin once.
- [ ] **H3** Process start/exit events (ETW or WMI) into an event timeline. *Done when:* "tell me when my build finishes" works.
- [ ] **H4** Pipe protocol between service and agent, with tests.

## I. Phase 2: two PCs (hub and mesh)

- [ ] **I1** Pick the hub host (our FastAPI hub or Claude Managed Agents) per `docs/ARCHITECTURE.md` section 3.6, and write the job protocol (job, result, heartbeat) between hub and PC workers.
- [ ] **I6** Hub process: device registry, task log (queued/running/done/failed), routes jobs to the right PC worker. needs: I1.
- [ ] **I2** Pairing with a QR code and per-device keys; transport over Tailscale or WebSocket with TLS.
- [ ] **I3** PC worker: wrap today's `windows-mvp` brain as a worker that runs whole jobs locally, sends heartbeats, and releases all keys and mouse buttons and stops if the hub goes silent. needs: I1.
- [x] **I4** Handoff: "send this tab/file/clipboard to my laptop". Files (`copy_file`, streamed) and clipboard (`send_clipboard`) done in #9; tabs are I7.
- [x] **I5** "What's my desktop doing?" from another PC. Done in #9: each PC pushes its active window and in-use state over the live feed (`jarvis/remote.py`).
- [ ] **I7** Tab handoff: "send this tab to my laptop" (needs the browser extension, or reading the URL bar through UI Automation).
- [ ] **I8** Whole-job delegation to another PC's brain (`ask_pc`), so long computer-use tasks run there without a network hop per step. Today (#9) the PC you talk to drives the other PC's tools one call at a time. This is the step toward the hub/worker split in I1/I3.
- [ ] **I9** Test multi-PC on two real Windows PCs over Tailscale and a home LAN: discovery, firewall prompt, `copy_file` speed, reminders coming back. Add the steps to TESTING.md as a manual scenario.
- [x] **I10** Multi-PC safety (`jarvis/remote.py`): the PC being controlled asks the person sitting at it before consequential calls and file writes (`remote.set_local_confirm`, set by the engine and the console; `JARVIS_REMOTE_TRUST_CALLER=1` there opts out). File reads and writes from other PCs go in its audit log; received files land in Downloads, never in Alfred's folders or over an existing file without `overwrite`, and only with room on the disk. Serving binds to the Tailscale or LAN address (never `0.0.0.0` by default) with a connection cap, a socket timeout and an exclusive port; the token must be 16+ characters, lives in the secret store and is never logged (`--show-token` to pair). Reminders go back only to the PC that set them, other PCs' status reaches Claude as an untrusted block, and a dropped `POST /call` is never resent. Tests: `tests/test_remote.py`.

## J. Phase 3: deeper Windows features (no drivers)

- [ ] **J1** Study mode: per-app network allow/block with Windows Filtering Platform rules, plus Do Not Disturb. needs: H2.
- [ ] **J2** "Jarvis Drive": other PCs' files in File Explorer through the Cloud Files API. needs: I2.

## K. Packaging and deployment

- [ ] **K1** One-click installer (MSIX or Inno Setup) bundling Python and the lock file; auto-start at login.
- [ ] **K2** Tray icon with status, mute, settings and Stop.
- [ ] **K3** Code signing plan (certificate, SmartScreen).
- [x] **K4** Foundation for the Alfred desktop app: saved settings outside the install folder (`jarvis/settings.py`: `%LOCALAPPDATA%\Jarvis\settings.env`, secrets in Windows Credential Manager as `Alfred/<KEY>`), one engine process for every front end (`jarvis/engine.py`, protocol v2; `telegram_worker.py` is now a thin v1 wrapper over it), the in-process API the UI uses (`jarvis/service/`: `AlfredService`, `EventBus`, hard Stop through a Job Object, crash watchdog, `FakeService` for UI work without Windows), and Telegram in parent mode (`ALFRED_BRIDGE_MODE=parent`) so Telegram runs on the app's engine. Tests: `tests/test_settings.py`, `tests/test_service.py`, `telegram/tests/parent.test.mjs`.
- [x] **K5** Desktop app window on `jarvis.service.AlfredService` (develop against `jarvis.service.fake.FakeService`): chat, status, approvals, Stop, settings (secrets shown only as `Store.secret_hint`), Telegram on/off and owner pick from `telegram_user` events. needs: K4. *Done:* `python -m jarvis.app [--fake-core] [--no-window] [--port N]` (`windows-mvp/DESKTOP.md`): `jarvis/app/server.py` (core API on `app/http.py`, local-only sign-in: 127.0.0.1, one-time link, HttpOnly SameSite=Strict cookie, Host, Origin and `X-Alfred` checks, strict CSP), `jarvis/app/main.py` (pywebview window, pystray tray with Open/Stop everything/Quit, closing keeps it in the tray, one instance per user, Ctrl+Alt+Backspace Stop hotkey), `jarvis/app/ui/` (conversation, STOP, status, approvals with countdown, data notice, settings with write-only keys, activity log). Tests: `tests/test_app_server.py`.
- [ ] **K6** Package `alfred-cli.exe --engine` (what `service.engine_command()` starts when frozen) and the Telegram bridge with its own `node.exe` (`service.bridge_command()`); first run imports an old `windows-mvp\.env` with `Store.import_legacy_env` and records consent with `consent.record("app")`. needs: K4, K1.
- [ ] **K7** On a real Windows PC, check that Stop and closing the app kill PowerShell and computer use but never apps Alfred opened for the user (they leave the engine's Job Object via `tools/apps.py:start_detached`), including when the app itself runs inside another job. needs: K4.
- [ ] **K8** Spec PR (SPEC_VERSION bump): add `pywebview`, `pystray` and `Pillow` to `requirements.txt` and `requirements.lock.txt`, and add `tests/test_app_http.py` and `tests/test_app_server.py` to `test.bat` step 2. Then, on a real Windows PC, check the native window, tray (Open, Stop everything, Quit, closing keeps it in the tray), the Ctrl+Alt+Backspace hotkey and a second launch bringing the window back. needs: K5.
- [x] **K9** First-run setup in the Alfred app, in a Frutiger Aero look. needs: K5. *Done:* `jarvis/app/ui/theme/aero.css` (reusable design system: tokens, glass, glossy buttons, red STOP, inputs, switches, progress, steps; light/dark, reduced motion, forced colors), `jarvis/app/ui/onboarding/` (10 steps: welcome and system check, data notice, old `.env` import, Anthropic key, safety, microphone and speech model download, OpenAI key, Telegram bot + link-by-code owner, pairing code and sharing, start at sign-in and "Try it"), `jarvis/onboarding/` (`/api/onboarding/*`, `onboarding.json`, key checks, Telegram link code, HKCU Run), `voice.py` loads the model from `models_dir()` and uses `JARVIS_MIC`; Telegram discovery mode passes the typed text. Tests: `tests/test_onboarding.py`, `telegram/tests/parent.test.mjs`. *Left:* on a real Windows PC, check the speech model download and Cancel, start at sign-in, the Firewall prompt with sharing on, and linking a real Telegram account; add `tests/test_onboarding.py` to `test.bat` with K8; the voice test needs `voice_test(mic)` on the service (task D, voice mode).

## L. Later phases (see README section 8)

- [ ] **L1** Phone app (voice, confirmations, notifications). Telegram (E) covers part of this for now.
- [ ] **L2** Mac node.
- [ ] **L3** Optional signed drivers (virtual mic, display), only if a requirement truly needs them.

## M. Demo and presentation

- [ ] **M1** Demo script: 5 commands that show each layer (router, UIA, COM, computer use, Telegram), with a backup recording.
- [ ] **M2** Pitch slides: problem, how deep Jarvis goes on Windows, safety model, live demo.
- [ ] **M3** Clean demo PC: run `sandbox\run-in-sandbox.bat` or a fresh setup, matching the spec fingerprint.
