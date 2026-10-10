# Jarvis-Hacks

An Iron Man–style "Jarvis": a voice-controlled assistant that sees and acts across all of your devices.

**Primary target: Windows ↔ Windows.** It gives the deepest system access with the fewest gatekeepers. Macs, iPhones and iPads join later as secondary nodes.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (general design) · [docs/APPLE_PLATFORM.md](docs/APPLE_PLATFORM.md) (Apple APIs and limits) · [docs/COMPUTER_USE_PLAN.md](docs/COMPUTER_USE_PLAN.md) (vision fallback) · [docs/CREDENTIALS_AND_USER_SECURITY.md](docs/CREDENTIALS_AND_USER_SECURITY.md) (passwords and user-side security)

**Contributing (people and agents):** start with the master task list [docs/TASKS.md](docs/TASKS.md) and the live board [docs/STATUS.md](docs/STATUS.md).

**Try it now:** the Phase 0 Windows MVP is in [windows-mvp/](windows-mvp/README.md). Run `setup.bat`, then `jarvis.bat`, and talk to it.

## 1. Goals

1. **Awareness:** Jarvis knows what's happening on every device: apps, windows, documents, system events, and which device you're using.
2. **Direct control:** Jarvis acts *inside* apps through their UI elements, scripting and plugin APIs. It doesn't move the mouse or read screenshots, and it works in several apps at once, in the background.
3. **Cross-device:** any device can see and command any other.
4. **Voice:** talk to Jarvis and hear a reply in under ~800 ms.

"Sync" means **awareness + on-demand viewing + remote action + handoff** (URLs, files, clipboard). Copying the live in-memory state of any app to another device isn't possible, so it's out of scope.

## 2. System architecture

```
                 ┌──────────────────────── HUB ("brain") ────────────────────────┐
  voice ───────▶ │ Voice gateway ─▶ Intent router ─┬─ fast path (no LLM, ~50 ms) │
                 │                                 └─ Orchestrator LLM (tools)   │
                 │ Device registry · Context store · Memory/timeline · Policy    │
                 │ Event bus · Audit log                                         │
                 └──────▲─────────────────────▲──────────────────────▲───────────┘
                        │ encrypted mesh (Tailscale / WebSocket + mTLS)
              ┌─────────┴──────────┐  ┌───────┴────────┐  ┌──────────┴─────────┐
              │ Windows PC (full)  │  │ Mac (full)     │  │ Phone (thin client)│
              │ agent + service    │  │ agent          │  │ voice, notifs,     │
              │ MCP tool server    │  │ MCP tool server│  │ confirmations      │
              └────────────────────┘  └────────────────┘  └────────────────────┘
                 + browser extension · cloud APIs · smart home (Home Assistant)
```

- **Device agents** expose their capabilities as **MCP tool servers** with a capability manifest. The hub's LLM is the MCP client.
- **The brain lives outside the PCs it controls.** The hub's orchestrator sends whole jobs to a worker on each PC, which runs the click-and-type loop locally. If a PC crashes, the hub keeps the task and memory and retries or reports. If the hub drops, workers stop and release the mouse and keyboard. Details: [ARCHITECTURE.md §3.6](docs/ARCHITECTURE.md#36-orchestrator-outside-the-pc-workers-on-the-pc-crash-resilience).
- **Hub** runs on an always-on PC or a small VM:
  - It tracks devices, live per-device state, and the device you're using (by latest input).
  - It plans multi-step tasks across devices.
  - CRDTs (data types that merge cleanly) handle shared data; hybrid logical clocks order events across devices.
- **Mesh:** Tailscale to start. Devices pair by QR code with per-device revocable keys. Phones are woken with push (APNs/FCM) because they can't hold sockets in the background.
- **Memory:** a searchable timeline of apps, docs, URLs and transcripts (SQLite/Postgres + vector index). It stores text rather than screenshots, is encrypted, has retention limits, and lets you exclude apps.

### Voice pipeline (target: under ~800 ms to first audio)

| Stage | Choice | Note |
|---|---|---|
| Wake word | openWakeWord | On-device, always on |
| End of speech | Silero VAD | Bad end-of-speech detection is the main reason assistants feel slow |
| Speech-to-text | faster-whisper or a streaming cloud service | Streams partial transcripts |
| Router | Rules or a small classifier | Simple commands skip the LLM entirely |
| LLM | Fast model for chat, larger one for plans | Speaks the first sentence while still generating |
| Text-to-speech | Piper or a cloud voice | Streams audio |

You can interrupt it mid-sentence (barge-in, which needs echo cancellation). Only one device answers when several hear you. The reply plays on the device you're using. Long tasks get an "On it" now and a report later.

## 3. Windows depth levels

| Level | Windows technology | Needs | Used for |
|---|---|---|---|
| 1 Cloud APIs | Microsoft Graph, Spotify, Slack, Gmail | OAuth | Mail, calendar, music, chat |
| 2 App scripting | **COM** (Word/Excel/PowerPoint/Outlook), PowerShell, WMI, URI protocols | user | Documents and settings with no UI |
| 3 UI Automation | **UIA** patterns and events | user (admin for elevated apps) | **Reading and controlling any app, in the background, in parallel** |
| 4 Screen and input | Windows Graphics Capture; input injection (fallback) | user | On-demand screenshots and streaming |
| 5 Privileged service | **Windows Service (SYSTEM) + ETW** | admin | System-wide process, file, registry and network events |
| 6 OS extension frameworks | **Filtering Platform rules, Cloud Files API**, user-mode drivers, virtual audio driver | admin; Microsoft signing for drivers only | Network control, "Jarvis Drive", virtual mic, monitor and input |
| 7 Custom kernel code | — | — | **Not used.** No requirement needs it, and it risks blue-screens. |

**Where app internals come from**, most to least reliable:
1. The app's plugin API (VS Code, Office add-ins, browser extension)
2. COM
3. The cloud API
4. The UI Automation tree
5. Vision on screenshots, as a last resort

A SYSTEM service does **not** see inside apps. It runs in session 0 with no desktop. Injecting into other processes or reading their memory is out of scope.

## 4. Per-PC architecture

```
┌──────────────────────────── Windows PC ─────────────────────────────┐
│  User-session agent  (your login session, levels 2–4)               │
│   UI Automation · COM / PowerShell · screen capture · clipboard     │
│   voice I/O · tray UI + confirmations · MCP tool server             │
│                │ local named pipe (ACL-restricted)                  │
│  Jarvis Service  (SYSTEM, levels 5–6)                               │
│   ETW consumer · network rules · Cloud Files "Jarvis Drive"         │
│   power / services / updates · optional signed drivers · hub link   │
└─────────────────────────────────────────────────────────────────────┘
```

| UI Automation action | Interface |
|---|---|
| Click a button or menu item | `InvokePattern` |
| Set field text (no keystrokes) | `ValuePattern.SetValue` |
| Read a document, selection or field | `TextPattern`, `ValuePattern` |
| Check, select, expand, scroll, window ops | `Toggle` / `SelectionItem` / `ExpandCollapse` / `Scroll` / `Window` |
| React to changes | Focus-changed, property-changed and structure-changed events |

| Level 6 feature | Technology | Driver / signing |
|---|---|---|
| Per-app network allow/block | Filtering Platform rules | None |
| "Jarvis Drive" (every PC's files in File Explorer) | Cloud Files API | None |
| Virtual monitor / virtual input | User-mode drivers | Microsoft attestation |
| Virtual microphone | Virtual audio driver | Microsoft attestation |

**Windows blocks, by design:**
- The lock screen, sign-in screen, admin-approval (UAC) prompts and Ctrl+Alt+Del.
- Elevated apps, unless the agent is also elevated or has UI-access rights.
- DRM video, protected processes, and the contents of other apps' encrypted traffic.
- UI Automation in games and custom-drawn UIs; vision is the fallback.
- Unsigned binaries, which antivirus will flag.

## 5. Example workflows

1. **"Reply to Sam on Slack that I'll be late"** while you're in Word. Uses the Slack API, or UI Automation sets the message box and invokes Send. Slack stays in the background.
2. **"Put the Excel numbers into the report."** COM reads the Excel range and writes a Word table. No UI.
3. **"Tell me when my build finishes."** ETW catches the process exit, UI Automation reads the terminal's last lines, and Jarvis speaks the result on the device you're using.
4. **From the laptop: "What's my desktop doing?"** Active app, window titles and recent events. "Show me" adds a screenshot.
5. **"Study mode for two hours."** Network rules block sites, PowerShell turns on Do Not Disturb, and Discord is closed.
6. **"Fill this form, rename my screenshots, pause the music,"** run in parallel through the browser extension, file APIs and Spotify API.

## 6. Security

- **Screen, email and web content is untrusted data, never instructions.** This is the main defense against prompt injection.
- **Permission tiers:**
  - Reading is automatic.
  - Low-risk actions (media, volume, windows) run automatically.
  - Send, delete, buy and post need confirmation (Windows Hello or a phone tap).
  - Passwords, payments and admin prompts are never automated. A later, user-approved password fill (Windows Hello, secret never shown to the model) is planned in [docs/CREDENTIALS_AND_USER_SECURITY.md](docs/CREDENTIALS_AND_USER_SECURITY.md).
- **Voice is not authentication.**
- Audit log of every action, plus a kill switch that stops all agents.
- Mutual TLS between devices, keys in Credential Manager / TPM, an ACL-restricted pipe, and code signing on every binary.

## 7. Deployment

- **Installer:** signed MSI/MSIX that installs the service, the user agent (auto-start at login) and any optional drivers.
- **Signing:** a code-signing certificate for the app. An **EV certificate + Microsoft Partner Center** are needed only for level 6 drivers; use test-signing on dev machines.
- **Privileges:** admin once at install. The service runs as SYSTEM; the agent runs elevated or with UI-access rights.

## 8. Build phases

| Phase | Scope | Done when |
|---|---|---|
| 0 Single PC | Agent: UI Automation, COM, PowerShell and capture tools over MCP; voice loop; local hub | "Open Spotify and play my focus playlist" works by voice |
| 1 Service + ETW | SYSTEM service, ETW, named-pipe bridge, event timeline | Build-finished alert works |
| 2 Two PCs | Hub orchestrator + PC worker job API (heartbeats, retries), pairing, mesh, registry, cross-PC routing, clipboard/tab handoff, browser extension | "Send that tab to my laptop" |
| 3 Level 6, no drivers | Network rules (study mode), Cloud Files "Jarvis Drive" | Every PC's files appear in File Explorer |
| 4 Hardening | Permission tiers, confirmations, audit log UI, kill switch, prompt-injection tests | Safe to run daily |
| 5 Drivers (optional) | Virtual audio, display and input, Microsoft-signed | Phone mic works as the PC's input |
| 6 Other devices | Phone app (voice, confirmations, notifications), Mac nodes | Control any PC from your phone |

**Stack:**
- Agent and service: C# / .NET 8, using `TraceEvent` for ETW. Python (`uiautomation`, `pywinauto`) is fine for prototyping.
- Drivers: C++ with the Windows Driver Kit.
- Hub: Python (FastAPI).
- Transport: WebSocket + JSON-RPC (MCP), with WebRTC for screen streams.
- Storage: SQLite/Postgres + vector index.

**Later additions:** smart home (Home Assistant), proactive alerts, Apple Watch/Wear OS approvals, an iPad/web dashboard, an offline local-LLM fallback.

**Cut:** streaming every screen all the time, building our own speech/LLM/VPN, full iPhone control (iOS forbids it), and vision as the main control path.
