# Live task board

_Auto-generated 2026-10-11 00:33 UTC from [TASKS.md](TASKS.md), open PRs and branches. Do not edit; changes are overwritten._

**Progress:** 22 of 85 tasks done.

## In progress (claimed)

| Task | Title | Claimed by |
|---|---|---|
| **0.7** | PR #6 (browser voice mode on OpenAI's live voice model, `jarvisvoice`): decide how it relates to area D (local voice) and E3 (voice service). Then give it a task ID, merge `main`, and add its tests to `test.bat`, or keep it as a separate app. | PR #10 (DhruvaValluru, `claude/funny-tesla-rxt8yr`, draft) |
| **0.8** | PR #7 (multi-PC control, peer-to-peer tool calls): reconcile with the hub/worker design in `docs/ARCHITECTURE.md` section 3.6 (tasks I1–I6) before merging. Decide: peer-to-peer now and the hub later, or the hub only. Then give it a task ID and merge `main`. | PR #10 (DhruvaValluru, `claude/funny-tesla-rxt8yr`, draft) |
| **I4** | Handoff: "send this tab/file/clipboard to my laptop". | PR #9 (DhruvaValluru, `communications`, draft) |
| **I5** | "What's my desktop doing?" from another PC. | PR #9 (DhruvaValluru, `communications`, draft) |
| **K1** | One-click installer (MSIX or Inno Setup) bundling Python and the lock file; auto-start at login. | PR #10 (DhruvaValluru, `claude/funny-tesla-rxt8yr`, draft) |

## Ready to start (open, unclaimed, dependencies done)

First 3 per area. Take the lowest number in your area.


**0. Integration (do these first; they unblock everyone)**

- **0.9** The assistant's name is **Alfred**. *Done:* what users see (Claude's self-name, console text, Telegram, data notice, MVP README; the wake word accepts "Alfred" and "Jarvis"). *Open:* decide whether to also rename code, scripts and the repo (`jarvis.bat`, `python -m jarvis`); that breaks every open branch, so do it only at a quiet moment.
- **0.6** Delete the stale branches `mvp` and `claude/sharp-bohr-sglils` (already merged). Ask the repo owner first.

**A. Core brain (Claude loop, routing, memory)**

- **A3** Stream Claude's reply and speak the first sentence before the whole reply is done.
- **A4** Per-turn effort: `low` for chat, `medium` when the turn uses computer use or more than 3 tools.
- **A5** Prompt caching check: log `cache_read_input_tokens`; fix anything that breaks the cache (tools order, system text).

**B. Windows tools (UI Automation, COM, apps)**

- **B2** Outlook COM: read the latest emails and draft (never send without confirmation).
- **B3** PowerPoint COM: add a slide with a title and bullets.
- **B4** Browser control through UIA for Edge and Chrome: read the current tab's URL and title, open a link in a new tab.

**C. Computer use (vision fallback) — merged from `eshaan-work`**

- **C5** Manual end-to-end run with a real key: Paint "draw a square", one browser task, one game or launcher. Record results in the PR.
- **C6** Confirm before pressing Enter in chat and email apps (Slack, Teams, Discord, Outlook, WhatsApp).
- **C7** Measure cost and latency per step; compare 1024 vs 1280 px. Write the numbers in `docs/COMPUTER_USE_PLAN.md`.

**D. Voice (local)**

- **D3** Better end-of-speech detection with Silero VAD instead of the loudness threshold.
- **D4** Real wake word (openWakeWord "hey jarvis") instead of transcribing everything.
- **D5** Barge-in: stop speaking when the user starts talking.

**E. Remote control (Telegram, phone) — merged from `Saatvik-Branch`**

- **E3** Voice service from `windows-mvp/VOICE_API.md` (`POST /speech`, `POST /transcribe`). *Current state:* the Telegram bridge calls OpenAI directly for voice notes and spoken replies; the custom service is optional. Decide whether to keep OpenAI or build the service, and record it here.
- **E4** Confirmations over Telegram: inline Yes/No buttons, with a 60 s timeout that defaults to No.
- **E5** Send screenshots to Telegram on request ("show me my screen").

**F. Safety and security (applies to every area)**

- **F2** Prompt-injection test suite: documents and pages telling Jarvis to "click Send" or "type your password".
- **F3** Kill switch that stops all agents and remote sessions at once (hotkey, tray, Telegram `/stop`).
- **F4** Per-app permissions (allow, ask, never) for UIA and computer use.

**G. Testing spec and CI**

- **G3** Claude scenario tests with a real key, run nightly in CI with a repo secret: the manual scenarios M1, M7 and M8 from TESTING.md, scripted.
- **G4** Make the `spec-test` CI check required on `main` (branch protection; repo owner).
- **G5** Self-hosted Windows 11 runner (a team PC) for tests that need audio, Office or a real desktop.

**H. Phase 1: background service and system events**

- **H1** Design doc: what the service does vs the user-session agent, and how they talk (named pipe with an ACL). Follow README section 4.
- **H2** Service skeleton (C#/.NET 8 or Python), with install and uninstall scripts that need admin once.
- **H3** Process start/exit events (ETW or WMI) into an event timeline.

**I. Phase 2: two PCs (hub and mesh)**

- **I1** Pick the hub host (our FastAPI hub or Claude Managed Agents) per `docs/ARCHITECTURE.md` section 3.6, and write the job protocol (job, result, heartbeat) between hub and PC workers.
- **I2** Pairing with a QR code and per-device keys; transport over Tailscale or WebSocket with TLS.

**K. Packaging and deployment**

- **K2** Tray icon with status, mute, settings and Stop.
- **K3** Code signing plan (certificate, SmartScreen).

**L. Later phases (see README section 8)**

- **L1** Phone app (voice, confirmations, notifications). Telegram (E) covers part of this for now.
- **L2** Mac node.
- **L3** Optional signed drivers (virtual mic, display), only if a requirement truly needs them.

**M. Demo and presentation**

- **M1** Demo script: 5 commands that show each layer (router, UIA, COM, computer use, Telegram), with a backup recording.
- **M2** Pitch slides: problem, how deep Jarvis goes on Windows, safety model, live demo.
- **M3** Clean demo PC: run `sandbox\run-in-sandbox.bat` or a fresh setup, matching the spec fingerprint.

## Blocked (waiting on dependencies)

- **I6** waits on I1
- **I3** waits on I1
- **J1** waits on H2
- **J2** waits on I2

## Needs attention

- PR #7 "Let one Jarvis control your other PCs" on `claude/zealous-ramanujan-1tzfmr`: add a task ID like `[C6]` to the title
- PR #6 "Add Jarvis voice: talk to Jarvis like ChatGPT voice mode" on `jarvisvoice`: add a task ID like `[C6]` to the title
