# Live task board

_Auto-generated 2026-10-10 19:32 UTC from [TASKS.md](TASKS.md), open PRs and branches. Do not edit; changes are overwritten._

**Progress:** 13 of 77 tasks done.

## In progress (claimed)

| Task | Title | Claimed by |
|---|---|---|

## Ready to start (open, unclaimed, dependencies done)

First 3 per area. Take the lowest number in your area.


**0. Integration (do these first; they unblock everyone)**

- **0.1** Merge PR #5 (shared test spec) into `main`.
- **0.6** Delete the stale branches `mvp` and `claude/sharp-bohr-sglils` (already merged). Ask the repo owner first.

**A. Core brain (Claude loop, routing, memory)**

- **A3** Stream Claude's reply and speak the first sentence before the whole reply is done.
- **A5** Prompt caching check: log `cache_read_input_tokens`; fix anything that breaks the cache (tools order, system text).
- **A6** Session memory: persist a short summary of each conversation to `%LOCALAPPDATA%\Jarvis\memory.jsonl`, and load the last N into context on start.

**B. Windows tools (UI Automation, COM, apps)**

- **B2** Outlook COM: read the latest emails and draft (never send without confirmation).
- **B3** PowerPoint COM: add a slide with a title and bullets.
- **B4** Browser control through UIA for Edge and Chrome: read the current tab's URL and title, open a link in a new tab.

**C. Computer use (vision fallback) — branch `eshaan-work`, PR #4**

- **C6** Confirm before pressing Enter in chat and email apps (Slack, Teams, Discord, Outlook, WhatsApp).
- **C7** Measure cost and latency per step; compare 1024 vs 1280 px. Write the numbers in `docs/COMPUTER_USE_PLAN.md`.
- **C8** Multi-monitor: capture the monitor the target window is on.

**D. Voice (local)**

- **D3** Better end-of-speech detection with Silero VAD instead of the loudness threshold.
- **D4** Real wake word (openWakeWord "hey jarvis") instead of transcribing everything.
- **D5** Barge-in: stop speaking when the user starts talking.

**E. Remote control (Telegram, phone) — branch `Saatvik-Branch`**

- **E2** Integration work in 0.4 and 0.5.
- **E3** Build the voice service from `windows-mvp/VOICE_API.md` (`POST /speech`, `POST /transcribe`), or decide to use OpenAI directly. Record the decision here.
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

- **I1** Hub process (FastAPI): device registry, routes commands to the right PC.
- **I2** Pairing with a QR code and per-device keys; transport over Tailscale or WebSocket with TLS.
- **I3** Expose each PC's tools as an MCP server; the hub's Claude calls tools on any PC.

**K. Packaging and deployment**

- **K1** One-click installer (MSIX or Inno Setup) bundling Python and the lock file; auto-start at login.
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

- **0.2** waits on 0.1
- **0.3** waits on 0.2
- **0.4** waits on 0.1
- **0.5** waits on 0.2, 0.4
- **A4** waits on 0.2
- **C5** waits on 0.2
- **E4** waits on 0.5
- **J1** waits on H2
- **J2** waits on I2

## Needs attention

- branch `Saatvik-Branch` (Saatvik Samtosh, no PR, 3 commits, last 2026-10-10): "Connect Telegram through Photon Spectrum and add voice service adapter". Open a draft PR with a task ID.
- branch `claude/funny-tesla-rxt8yr` (Claude, no PR, 1 commits, last 2026-10-10): "Add a shared test spec so every person and agent tests the same way". Open a draft PR with a task ID.
- branch `eshaan-work` (Claude, no PR, 3 commits, last 2026-10-10): "Document computer use and the user-side credentials/security plan". Open a draft PR with a task ID.
