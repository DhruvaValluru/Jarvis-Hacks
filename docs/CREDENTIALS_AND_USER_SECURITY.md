# Passwords, credentials and user-side security

**Status: planned, not the current focus.** Today Jarvis **never types passwords**. Computer use (`type`) and UI Automation (`ui_set_text`) both refuse to fill a password field. The system prompt tells Claude not to try, and Claude is asked to hand that step to the user. This document is the plan for later: letting the user approve credential use safely, plus the other protections that live on the user's side.

## Principles

1. **A secret never enters the model's context.** Claude never sees a password, so it can't leak it, repeat it, or be tricked into typing it somewhere else. It only sees a label like `github (personal)`.
2. **Every use needs the user's approval, and voice isn't enough.** The approval prompt shows which secret, which app or site, and which window. The user confirms with Windows Hello (face, fingerprint or PIN) or a phone tap.
3. **Each secret is bound to its target.** It has an allow-list of apps and web domains. If the focused window doesn't match, it isn't filled, even if the user approves. This stops a prompt-injected page from collecting a password meant for another site.
4. **Some things stay off-limits.** Payment card numbers, bank transfers, one-time 2FA codes, recovery codes, UAC/admin prompts and the Windows sign-in screen stay manual. These match the "never automated" tier in the main [README](../README.md#6-security).

## Planned design: user-approved credential fill

```
Claude: fill_credential(label="github", field="password")       <- label only, never the value
  -> Jarvis checks: does the focused window/domain match the secret's allow-list?
  -> Jarvis shows a Windows Hello prompt: "Fill GitHub password into Chrome: github.com/login?"
  -> user approves -> Jarvis reads the secret from the vault and sets it straight into the field
  -> Claude gets back only "filled" or "declined"
```

| Piece | Plan |
|---|---|
| Vault | **Windows Credential Manager** (`CredWrite`/`CredRead`, protected by DPAPI and the user's login), one entry per secret under a `Jarvis/` prefix. Later: read from an existing password manager (1Password / Bitwarden CLI) instead of storing our own copy. |
| Enrolment | The user adds secrets in a small local settings UI or `jarvis --add-credential`. Never by voice and never through Claude. Each entry stores a label, username, secret, and allowed apps/domains. |
| Fill tool | New `fill_credential(label, field)` tool at the `confirm` risk tier. It always asks, with no "remember my choice". It fills through UIA `ValuePattern.SetValue` on the focused password field, or Unicode `SendInput` as a fallback. It never uses the clipboard. |
| Target check | Read the focused element via UIA: it must be `IsPassword` (or a username field for `field="username"`). Its window's process must be on the allow-list. For browsers, read the address bar or use the browser extension (phase 2) to check the domain. |
| Approval | Windows Hello via `UserConsentVerifier.RequestVerificationAsync`, falling back to the console y/N prompt only when Hello isn't set up. Later, a phone approval through the mesh (phase 6). |
| Logging | The audit log records the label, target window and the decision. It **never** records the value. Screenshots are already safe because password fields show dots, but no screenshot is taken in the same turn as a fill. |
| Rate limits | At most N fills per minute, plus a cool-down after a declined prompt so repeated injection attempts can't wear the user down. |

**2FA and one-time codes.** Jarvis says "Please enter the code from your authenticator" and waits. It doesn't read SMS codes or authenticator apps.

## Other user-side security work (planned)

| Item | What the user gets |
|---|---|
| **Visible "Jarvis is driving" indicator** | A tray icon state plus a thin coloured border overlay while computer use controls the mouse and keyboard, so it's never invisible. |
| **Kill switch** | ✅ Built now: moving the mouse into the top-left corner stops computer use mid-task, and Ctrl+C stops everything. Planned: a global hotkey and a "Stop" button on the overlay. |
| **Per-app permissions** | Settings with allow, ask or never per app for computer use (for example "never control my banking app" or "always ask in Outlook"). Screenshots are skipped when a never-app is in front. |
| **Screen privacy** | Don't screenshot while a password manager, banking site or other excluded window is in front. Tell the user that screenshots go to the Claude API, and give them a setting to turn computer use off (`JARVIS_COMPUTER_USE=0`, built now). |
| **Confirmation UX** | Replace the console y/N prompt with a toast or tray prompt that shows what will be clicked or sent. Approval works with Windows Hello for the `confirm` tier. |
| **Audit log viewer** | A simple local page that lists every action (from `%LOCALAPPDATA%\Jarvis\audit.jsonl`), with filters and a "report something wrong" button. |
| **Prompt-injection tests** | A test suite of pages and documents that tell Jarvis to "type your password here" or "click Send". The tests pass only when Jarvis refuses or asks. |

## What's already in place (computer use PR)

- `type` (computer use) and `ui_set_text` (UIA) refuse password fields (UIA `IsPassword`).
- Clicks on controls named Send, Delete, Buy, Post, Submit and similar ask first, for both UIA clicks and pixel clicks (the control under the cursor is looked up through UIA).
- Computer use stops on the lock screen, UAC and other secure desktops.
- Corner kill switch, plus an audit log entry for every action with real screen coordinates and the control that was clicked.
