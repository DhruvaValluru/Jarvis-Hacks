# Jarvis-Hacks

An Iron Man–style "Jarvis": a voice-controlled assistant that sees and acts across all of your devices.

**Primary target: Windows ↔ Windows.** Windows gives the deepest system access with the fewest gatekeepers. Macs, iPhones and iPads can join later as secondary nodes.

Other design docs:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): general design (hub, voice pipeline, memory, security)
- [docs/APPLE_PLATFORM.md](docs/APPLE_PLATFORM.md): Apple-specific APIs, permissions and limits

---

## 1. Goals

1. **Awareness.** Jarvis always knows what's happening on every PC: apps, windows, documents, activity, system events.
2. **Direct control.** Jarvis acts *inside* apps by calling their UI elements, scripting and plugin APIs directly. It doesn't move the mouse or read screenshots, and it can work in several apps at once, in the background.
3. **Cross-device.** Any device can see and command any other.
4. **Voice.** Talk to Jarvis and get a spoken response in under a second.

**Rule: go only as deep as a requirement needs.** No requirement needs custom kernel code. The deepest layer we use is the set of Windows-approved extension frameworks (level 6), and only for specific features.

---

## 2. Depth levels on Windows

| Level | What it is | Windows technology | Needs | Jarvis uses it for |
|---|---|---|---|---|
| 1 | Cloud APIs | Microsoft Graph, Spotify, Slack, Gmail… | OAuth | Mail, calendar, music, chat |
| 2 | App scripting | **COM automation** (Word, Excel, PowerPoint, Outlook), PowerShell, WMI, URI protocols | user | Editing documents and data with no UI involved; system settings |
| 3 | Accessibility / UI Automation | **UI Automation (UIA)** element patterns and events | user (admin to control elevated apps) | **Reading and controlling any app's UI, in the background, in parallel** |
| 4 | Screen and input | Windows Graphics Capture; input injection (fallback only) | user | On-demand screenshots and streaming; fallback for apps without UIA |
| 5 | Privileged service | **Windows Service (SYSTEM)** + **Event Tracing for Windows (ETW)** | admin | System-wide process, file, registry and network events; power, services |
| 6 | OS-approved extension frameworks | **Windows Filtering Platform** rules, **Cloud Files API**, user-mode drivers (virtual display, input), virtual audio driver | admin; Microsoft signing for drivers only | Network control, "Jarvis Drive", virtual mic, monitor and input |
| 7 | Custom kernel code | — | — | **Not used.** No requirement needs it. It risks blue-screens and has heavy signing requirements. |

### Where "what an app is working on" comes from
Ranked from most to least reliable:
1. **The app's own plugin or extension API:** VS Code extensions, Office add-ins, browser extension.
2. **COM / scripting interfaces:** the full Office document model.
3. **Cloud API** of the service.
4. **UI Automation tree:** works on almost any app with no cooperation from it.
5. **Vision on screenshots:** last resort, for games and custom-drawn UIs.

**A SYSTEM service (level 5) does *not* see inside apps.** It sees processes, files and network activity, and it runs in session 0 with no access to the desktop. App-level insight comes from the user-session agent (levels 2 to 4).

**Out of scope:** injecting code into other processes or reading their memory. It breaks with every app update, gets flagged as malware, and levels 1 to 6 already provide real app internals.

---

## 3. Per-PC architecture

Each Windows PC runs two cooperating processes. They talk over a local named pipe that only these two processes can access.

```
┌──────────────────────────── Windows PC ─────────────────────────────┐
│                                                                     │
│  User-session agent  (runs in your login session, levels 2–4)       │
│   • UI Automation reader/actor   • COM / PowerShell bridges         │
│   • Screen capture (on demand)   • Clipboard, active window         │
│   • Voice I/O (mic, wake word, TTS)  • Tray UI + confirmations      │
│   • Exposes tools as an MCP server                                  │
│                │ local named pipe (ACL-restricted)                  │
│  Jarvis Service  (Windows Service as SYSTEM, levels 5–6)            │
│   • ETW consumer: process/file/registry/network events              │
│   • WFP network rules         • Cloud Files "Jarvis Drive"          │
│   • Power, services, updates  • Optional signed drivers (level 6)   │
│   • Encrypted link to the hub / other PCs                           │
└─────────────────────────────────────────────────────────────────────┘
                 │  encrypted mesh (Tailscale / WebSocket + mTLS)
        ┌────────┴────────┐
        │  Hub ("brain")  │  device registry · context store · orchestrator LLM
        │  (one PC or VM) │  intent router · memory/timeline · policy + audit log
        └─────────────────┘
```

---

## 4. Capabilities by level

### Level 2: COM, PowerShell, WMI
- **Word / Excel / PowerPoint / Outlook:** read and write documents, cells, slides, emails and calendar through their object models. No UI is involved.
- **PowerShell / WMI:** processes, services, network adapters, display, power plans, installed apps, scheduled tasks, Windows settings.
- **URI protocols:** `ms-settings:`, `spotify:`, `msteams:` and others open specific screens.

### Level 3: UI Automation (the main way to control apps)
| Action | UI Automation interface |
|---|---|
| Click a button or menu item | `InvokePattern` |
| Set the text in a field (no keystrokes) | `ValuePattern.SetValue` |
| Read a document, selection or field | `TextPattern`, `ValuePattern` |
| Check, select, expand | `TogglePattern`, `SelectionItemPattern`, `ExpandCollapsePattern` |
| Scroll, minimize, close, move | `ScrollPattern`, `WindowPattern`, `TransformPattern` |
| React to changes | Focus-changed, property-changed and structure-changed events |

- Works on **background windows** and on **several apps in parallel**. It doesn't use the mouse.
- Works across Win32, WPF, WinForms, UWP/WinUI, Chromium and Electron apps. Electron apps expose their accessibility tree when an assistive client is present.

### Level 4: screen and input
- Windows Graphics Capture for screenshots and streaming of a single window or the whole screen, **on demand only**.
- Simulated input is a **fallback** for apps without UI Automation support.

### Level 5: privileged service and ETW
- **ETW (no driver needed):**
  - Process start and exit, with command lines
  - File create, write and rename
  - Registry changes
  - Network connections per process
  - Many app- and OS-specific event sources
- Service control, power (sleep, wake, shutdown, lock), Windows Update status, device inventory.

### Level 6: OS-approved extension frameworks
| Feature | Technology | Driver? | Signing |
|---|---|---|---|
| Per-app network allow/block ("study mode") | Windows Filtering Platform rules | No | — |
| "Jarvis Drive": every PC's files in File Explorer | Cloud Files API (how OneDrive works) | No | — |
| Virtual monitor (stream or extend to another device) | Indirect display driver (user-mode) | Yes | Microsoft attestation |
| Virtual input devices | User-mode driver | Yes | Microsoft attestation |
| Virtual microphone (phone or another PC as mic) | Virtual audio driver | Yes (usually kernel) | Microsoft attestation |
| Deep packet inspection | Filtering Platform callout driver | Yes (kernel) | **Not planned** |

---

## 5. What we can't do on Windows (by design)

- Automate the **lock screen, sign-in screen, admin-approval (UAC) prompts or Ctrl+Alt+Del**. They run on a separate secure desktop.
- Control **elevated (admin) apps from a non-elevated agent**. The user-session agent must run elevated, or be signed and installed in Program Files with UI-access rights.
- Capture **DRM-protected video** (it renders black) or touch **protected processes** such as antivirus.
- Read the **contents of other apps' encrypted network traffic**. We see destinations, not contents.
- Use UI Automation in **games and some custom-drawn UIs**. Vision is the fallback.
- **Ship unsigned.** Antivirus will flag an automation agent, so all binaries are code-signed.

---

## 6. Example workflows

1. **"Reply to Sam on Slack that I'll be 10 minutes late"**, while you're typing in Word.
   - Use the Slack API if available.
   - Otherwise Jarvis finds the message box through UI Automation, sets its value and invokes Send. Slack never comes to the front.
2. **"Put this quarter's numbers from Excel into the report."** COM reads the Excel range and writes a table into Word. No UI.
3. **"Tell me when my build finishes."** ETW catches the build process exiting, UI Automation reads the terminal's last lines, and Jarvis speaks the result on the device you're using.
4. **From the laptop: "What's my desktop doing?"** Combines the active app, window titles and recent ETW activity. "Show me" adds a screenshot.
5. **"Open the PDF I'm reading on the desktop, on my laptop, same page."** UI Automation gives the document and page, the Jarvis Drive provides the file, and the laptop opens it at that page.
6. **"Study mode for two hours."** Filtering Platform rules block chosen sites, PowerShell turns on Do Not Disturb and closes Discord, and a timer reports back.
7. **"Fill in this form, rename my screenshots by date, and pause the music"**, all running in parallel:
   - the browser extension fills the form
   - file APIs rename the screenshots
   - the Spotify API pauses playback
8. **"Use my phone as the mic"** (later phase): a virtual audio driver receives the phone's audio stream.

---

## 7. Security

- **Treat what's on screen as untrusted.** Text Jarvis reads from screens, emails and web pages is data, never instructions. This is the main defense against prompt injection.
- **Permission tiers:**
  - Reading is automatic.
  - Low-risk actions (media, volume, windows) run automatically.
  - Sending, deleting, buying or posting needs confirmation on a trusted device (Windows Hello or a phone tap).
  - Passwords, payments and admin prompts are never automated.
- **Voice is not authentication.**
- **Audit log** of every action: device, tool, triggering request and result. **Kill switch** to stop all agents at once.
- **Encrypted transport:** mutual TLS between devices, a per-device revocable key stored in the Windows Credential Manager / TPM, and an ACL-restricted named pipe between service and agent.
- **Code signing** on every binary. Drivers are only added through Microsoft's attestation signing.

---

## 8. Deployment

| Item | Requirement |
|---|---|
| Installer | Signed MSI or MSIX; installs the service, the user-session agent (auto-start at login) and optional drivers |
| Code signing | Code-signing certificate for the app; **EV certificate + Microsoft Partner Center** only for level 6 drivers |
| Driver development | Microsoft's documented test-signing setup on your own dev machines; attestation signing for release |
| Privileges | Admin once at install. The service runs as SYSTEM; the agent runs in the user session, elevated or with UI-access rights. |
| Updates | Signed auto-updater |

---

## 9. Build phases

| Phase | Scope | Done when |
|---|---|---|
| **0: Single PC** | User-session agent: UI Automation, COM, PowerShell and screen-capture tools as an MCP server; voice loop (wake word → speech-to-text → LLM → text-to-speech); hub running locally | "Jarvis, open Spotify and play my focus playlist" works by voice |
| **1: Service + ETW** | Jarvis Service (SYSTEM), ETW consumer, named-pipe bridge, event timeline | "Tell me when my build finishes" works |
| **2: Two PCs** | Pairing, encrypted mesh, device registry, cross-PC tool routing, clipboard and tab handoff, browser extension | "What's open on my desktop? Send that tab to my laptop." |
| **3: Level 6, no drivers** | Filtering Platform rules (study mode), Cloud Files "Jarvis Drive" | Files from every PC appear in File Explorer |
| **4: Hardening** | Permission tiers, confirmations, audit log UI, kill switch, prompt-injection tests, signed installer | Safe to leave running daily |
| **5: Level 6 drivers (optional)** | Virtual audio (phone as mic), virtual display, virtual input; Microsoft-signed | Phone mic works as the PC's input |
| **6: Other devices** | Phone app (voice terminal, confirmations, notifications), Mac nodes | Control any PC from your phone |

### Suggested stack
- **User-session agent:** C# / .NET 8.
  - UI Automation, COM and WinRT are first-class in .NET.
  - Python with `uiautomation` / `pywinauto` / `comtypes` is fine for fast prototyping.
- **Service:** C# / .NET Worker Service, using `Microsoft.Diagnostics.Tracing.TraceEvent` for ETW.
- **Drivers (phase 5):** C++ with the Windows Driver Kit.
- **Hub:** Python (FastAPI) or .NET.
- **Voice:**
  - Wake word: openWakeWord
  - End-of-speech detection: Silero VAD
  - Speech-to-text: faster-whisper or a streaming cloud service
  - Text-to-speech: Piper or a cloud voice
- **Transport:** Tailscale to start; WebSocket + JSON-RPC (MCP) between hub and agents; WebRTC for screen streams.
- **Storage:** SQLite or Postgres + a vector index.
