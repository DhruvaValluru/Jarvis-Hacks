# Jarvis on Apple: System-Level Breakdown

Scope: **Apple devices only.** That means macOS, iOS, iPadOS and (optionally) watchOS. This document covers what each OS lets Jarvis do, the system-level depth each requirement needs, and the Apple-native pieces you get for free.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the general design (hub, agents, voice pipeline, security).

---

## 1. Same OS vs. mixed OS: what actually changes

The **network communication itself does not change.** A WebSocket message from Windows to a Mac is exactly as fast and as easy as one from a Mac to a Mac. TCP, JSON and encryption don't care about the OS.

What changes is everything *around* the wire:

| | Mixed OS (e.g. Windows ↔ Mac) | Apple only |
|---|---|---|
| Agent code | One codebase per OS, each with different automation APIs and permission models | One Swift codebase, shared across macOS/iOS/iPadOS/watchOS |
| Shared vocabulary | You must normalize concepts: app IDs (`.exe` path vs bundle ID), windows, file paths, keyboard shortcuts (Ctrl vs ⌘) | Same concepts everywhere |
| Identity and pairing | You build accounts and device pairing yourself | Apple ID, iCloud Keychain and CloudKit give you identity and key sync |
| Built-in sync | Nothing; you rebuild clipboard, file transfer and handoff | Universal Clipboard, AirDrop, Handoff and iCloud Drive already exist |
| Testing | Test matrix multiplies | Smaller matrix |

Two conclusions:

- **Mac ↔ Mac is the easiest pairing of all.** Both ends are full-power nodes with the same APIs.
- **iPhone ↔ Mac is harder than Windows ↔ Mac**, even though both are Apple. The difficulty depends on **how much each endpoint lets you see and do**, not on whether the OSes match. Windows and macOS are both permissive; iOS is locked down.

So going Apple-only saves development effort and gives you ecosystem glue for free. It does **not** remove the main constraint, because iOS is the most restrictive platform you could pick.

---

## 2. Device roles

| Device | Role | Why |
|---|---|---|
| **Mac (ideally an always-on Mac mini or desktop)** | **Hub plus full agent** | It's the only Apple OS that lets a third-party app read and control other apps. It also acts as a proxy for things iOS forbids, like iMessage. |
| **Other Macs** | Full agents | Same capabilities as the hub, minus the brain |
| **iPhone** | Thin client: voice terminal, notifications and confirmations, Shortcuts-driven sensors and actions | Sandboxed; can't see or control other apps |
| **iPad** | Same as iPhone, plus a natural "HUD" dashboard screen | Same sandbox as iPhone |
| **Apple Watch** | Push-to-talk, haptic confirmations ("Approve sending this email?") | Always on your wrist; good for quick approvals |

---

## 3. macOS: full node

**Everything Jarvis needs on the Mac is user-space with permissions.** It needs no kernel extensions, no private APIs and no disabling of SIP. That's important: private APIs break on OS updates.

| Need | API / mechanism | Permission (TCC) |
|---|---|---|
| Know which app and window is active, launch/quit/focus apps | `NSWorkspace` (running apps, activation notifications), `NSRunningApplication` | none |
| Read UI contents of any app (text, buttons, fields) | Accessibility API (`AXUIElement`) | **Accessibility** |
| Click, type, press shortcuts in any app | `AXUIElementPerformAction`, `CGEvent` posting | **Accessibility** |
| Script apps that support it (Safari, Mail, Music, Finder, Messages, Notes…) | Apple Events: AppleScript, JXA, `NSAppleScript` / ScriptingBridge | **Automation** (prompted once per target app) |
| Screenshots and screen streaming | **ScreenCaptureKit** | **Screen & System Audio Recording**. Recent macOS versions periodically ask the user to re-confirm this. |
| Run user's Shortcuts (HomeKit scenes, Messages, Focus, etc.) | `shortcuts run "<name>"` CLI | depends on the shortcut |
| Always-on mic for the wake word | `AVAudioEngine` | **Microphone** |
| On-device speech-to-text | `SpeechAnalyzer` (macOS 26+) or `SFSpeechRecognizer`; or Whisper | **Speech Recognition** (Apple's APIs) |
| On-device LLM for fast intent routing | **Foundation Models** framework (macOS 26+, Apple Intelligence devices) | none |
| Text-to-speech | `AVSpeechSynthesizer`, or a third-party TTS | none |
| Clipboard | `NSPasteboard` | Newer macOS versions are adding prompts for programmatic clipboard reads. Test this on your target version. |
| Watch files | FSEvents | **Full Disk Access** for protected locations |
| **iMessage read** | Read `~/Library/Messages/chat.db` (SQLite) | **Full Disk Access** |
| **iMessage send** | AppleScript to Messages.app | **Automation** (Messages) |
| Talk to other devices on the LAN | Network.framework, Bonjour | **Local Network** (prompted on recent macOS) |
| Global hotkey | `CGEventTap` or a hotkey API | **Input Monitoring** (only if listening to all keys) |
| Run at login, stay in background | Menu-bar app plus `SMAppService` login item / LaunchAgent | user approves login item |

**Known gaps on macOS:**
- **Other apps' notifications.** There's no public API to read them. Workarounds (reading the Notification Center UI via Accessibility, or its internal database) are fragile and break between releases.
- **"Now playing" for arbitrary apps.** Apple restricted the private MediaRemote framework for third parties in macOS 15.4. Use AppleScript for Music and Spotify, and their web APIs.
- **Apps with poor accessibility trees** (some Electron apps, games, canvas-based UIs). Fall back to screenshots plus vision.

**Distribution:** sign with Developer ID and **notarize**. It **cannot** go on the Mac App Store, because the App Sandbox blocks controlling other apps through Accessibility.

---

## 4. iOS / iPadOS: thin node

**The hard limit:** a third-party iOS app **cannot see or control other apps.** Not with any permission, entitlement or MDM profile. The only way around that is a jailbreak, which isn't viable on current iOS and defeats the security model. Design around it.

What you *can* do, mapped to the requirements:

### 4.1 Talking to Jarvis (voice)
There's no always-listening custom wake word in the background. "Hey Siri" is reserved. Your entry points are:
- **"Hey Siri, ask Jarvis …"**: expose your commands as **App Intents**. Siri and Apple Intelligence can invoke them by voice, even from the Lock Screen.
- **Action Button** (iPhone 15 Pro and later): map it to a "Talk to Jarvis" intent. This is the closest thing to a hardware Jarvis button.
- **Control Center / Lock Screen controls** (iOS 18+) and **widgets**.
- **Apple Watch** push-to-talk.
- **AirPods**: route audio through them once a session starts.
- **In-app**: when the app is open (or started an audio session in the foreground with the background audio mode), it can keep listening. Expect battery drain, the orange mic indicator, interruption by phone calls, and App Review scrutiny.

### 4.2 Knowing what's happening on the phone ("awareness")
- **Shortcuts personal automations** are the key trick. Triggers include *app opened/closed, time of day, arrive/leave location, Wi-Fi joined, Focus changed, charger connected, battery level, NFC tag* and others. Each automation can call your App Intent or send an HTTP request to the hub. Many can now run without asking for confirmation. This is how the iPhone reports "user opened Maps" without being able to read Maps.
- **Your own app's data:** location (with permission), motion, battery, network, Focus status (Focus filters), HealthKit, calendar and reminders (EventKit), contacts, photos.
- **Screen viewing on demand:** a **ReplayKit Broadcast Upload Extension**. The user starts it from Control Center, then the screen streams to the hub. It has a tight memory limit (~50 MB), so stream compressed frames and keep the extension lean.
- **Other apps' notifications: not available on iOS.** Hardware workaround: iPhone exposes notifications over BLE through **ANCS** (Apple Notification Center Service) to paired accessories, which is how smartwatches get them. An ESP32-class accessory could relay them to the hub. This is a hack, so keep it optional.
- **iMessages: not readable on iOS.** Read them on the Mac (Section 3).

### 4.3 Doing things on the phone (actions)
- **App Intents** in your own app, callable by Siri, Shortcuts and Spotlight.
- **Shortcuts** can drive many system and third-party actions (HomeKit, Messages, music, Focus, settings toggles, many apps' own intents). To trigger one from the hub, send a push the user taps, which opens `shortcuts://run-shortcut?name=…`. Or use an automation trigger such as a time, a NFC tag, or a Focus change.
- **URL schemes / universal links** open specific screens in other apps.
- **HomeKit framework:** full control of smart-home devices from your app.
- **Prefilled compose sheets** for Messages and Mail. The user taps Send.
- You **cannot** tap buttons in other apps, type into them, or read their screens.

### 4.4 Staying connected
- iOS suspends background apps, so there's **no persistent socket.**
- **Hub → iPhone** goes over **APNs**: visible or time-sensitive notifications always arrive. Silent pushes are throttled and best-effort.
- **iPhone → hub:** send when the app, an intent, an automation or a notification action runs.
- **Live Activities / Dynamic Island** show the progress of a long-running Jarvis task ("Exporting video on Mac Studio… 62%"). They can be updated by push.
- `BGTaskScheduler` for opportunistic background refresh. The system decides when it runs.

### 4.5 Distribution
- **App Store:** you'll be scrutinized on background audio, remote-control features and data collection.
- **Personal use:** TestFlight (builds expire after 90 days), or a paid developer account installing directly. Free provisioning expires every 7 days.

---

## 5. Apple-native glue: use it, don't rebuild it

| Need | Apple component |
|---|---|
| Device identity and user account | Apple ID / Sign in with Apple |
| Sync pairing keys across devices | iCloud Keychain |
| Slow-changing shared state (device registry, preferences, memory) | **CloudKit** (private database, push subscriptions) |
| Real-time commands (sub-second) | Your own WebSocket through the hub, **not** CloudKit (CloudKit latency is seconds) |
| LAN discovery and direct device-to-device | Network.framework + Bonjour; Multipeer Connectivity (iOS: foreground only) |
| Clipboard sync | Universal Clipboard (built in) |
| File transfer | AirDrop, iCloud Drive |
| Continue an activity on another device | **Handoff** (`NSUserActivity`). Works for your own app and apps that adopt it. |
| Use the iPad as a second Mac display | Sidecar |
| See the iPhone on the Mac | iPhone Mirroring (macOS 15+). Useful to the user, but there's no API for Jarvis. |
| Confirm sensitive actions | Face ID / Touch ID (`LocalAuthentication`), Apple Watch double-tap |
| Secure keys | Secure Enclave |
| On-device LLM and speech | Foundation Models, SpeechAnalyzer (OS 26+) |
| Smart home | HomeKit |
| Code sharing | Swift Package shared by all targets; SwiftUI multiplatform app |

---

## 6. Requirement → required system depth

| Requirement | macOS depth | iOS/iPadOS depth | Feasible? |
|---|---|---|---|
| See what's happening in any app | Accessibility + ScreenCaptureKit | Shortcuts automation events + ReplayKit on demand | Mac **yes**; iOS **partial** |
| Control any app | Accessibility + CGEvent + AppleScript | Only App Intents / Shortcuts / URL schemes | Mac **yes**; iOS **no**, only what Shortcuts exposes |
| Voice, "immediate" | Always-on mic, local wake word | Siri / Action Button / Watch / foreground | Mac **yes**; iOS via Siri/Action Button |
| Device A sees device B | Hub relays Mac state to any device | iPhone can *view* everything; can only be *viewed* via events + ReplayKit | **Yes**, asymmetric |
| Cross-device coordination | WebSocket mesh + Bonjour | APNs wake + intents | **Yes** |
| Handoff / continuity | Handoff, AirDrop, Universal Clipboard | Same | **Yes** for adopting apps, URLs and files |
| iMessage | Full read/send via Mac | Through the Mac only | **Yes** via Mac |
| Smart home | Shortcuts CLI / HomeKit via Catalyst | HomeKit | **Yes** |

**Bottom line:** the Mac does the heavy lifting and the iPhone/iPad are the remote and the screen. That actually matches the fiction: Jarvis lives in the house, and you talk to it from anywhere.

---

## 7. Apple-only build order

1. **Mac agent plus hub (one Swift menu-bar app):**
   - Accessibility and ScreenCaptureKit tools
   - Voice loop: wake word, SpeechAnalyzer or Whisper, LLM, TTS
   - Expose the tools over MCP
2. **Second Mac:** Bonjour discovery, pairing keys in iCloud Keychain, cross-Mac commands, iMessage tools.
3. **iPhone app:**
   - App Intents ("ask Jarvis"), Action Button, push notifications with approve/deny actions, Live Activities
   - Starter Shortcuts automations that report events to the hub
4. **iPad HUD:** dashboard of all devices, the action log, and the kill switch. **Watch:** push-to-talk and approvals.
5. **ReplayKit screen sharing, HomeKit, memory/timeline.**
