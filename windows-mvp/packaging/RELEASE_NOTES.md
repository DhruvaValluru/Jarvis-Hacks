<!-- The notes on every GitHub release of Alfred. The release job in .github/workflows/alfred-desktop.yml fills in
the version, the installer's SHA-256 and the commit, and adds the "notes" typed in Run workflow at the end. -->
Alfred is an assistant for your Windows PC. You type or say what you want, like "open Notepad and write a haiku about Mondays" or "put the Excel numbers into my report as a table", and Alfred does it in your apps. Claude plans the steps. Alfred asks before anything that sends, deletes, buys or posts, and its STOP button is always on screen.

**Just want to look first?** Try the web demo. It runs in your browser on a simulated PC, with nothing to install: https://dhruvavalluru.github.io/Jarvis-Hacks/

## What you need

- Windows 10 version 1809 or later, or Windows 11, 64-bit (x64).
- An Anthropic API key. Get one at https://platform.claude.com/settings/keys (pay as you go, separate from a Claude.ai subscription). Without a key, Alfred only understands simple commands like "open notepad" or "volume 30".
- Optional: an OpenAI API key, for voice mode and Telegram voice notes.
- Optional: Telegram, to text Alfred from your phone.

No admin rights needed. Alfred's window uses the Microsoft Edge WebView2 Runtime: Windows 11 has it, and if your PC doesn't, the installer offers to add it.

## Install

1. Under **Assets** below, download `Alfred-Setup-{{VERSION}}.exe`.
2. Double-click it. It installs for your Windows account only.
3. Alfred isn't code-signed yet, so Windows may say **"Windows protected your PC"**. Click **More info**, check that the app is `Alfred-Setup-{{VERSION}}.exe`, then click **Run anyway**. (If your browser says the file isn't commonly downloaded, choose **Keep**.)
4. Follow the wizard. Leave **Launch Alfred** ticked at the end.
5. Alfred opens a short setup: the data notice, your Anthropic key, safety, then optional steps for voice, OpenAI, Telegram and your other PCs.

To uninstall: Settings > Apps > Installed apps (Windows 10: Apps & features) > **Alfred** > **Uninstall**. It asks whether to also remove your settings and saved keys.

## What it can do

- Open apps, and read and change what's in them without touching your mouse (UI Automation, and Office for Word and Excel).
- Use the mouse and keyboard itself for apps that can't be reached that way, like Paint or a game (computer use).
- Run simple commands instantly, without Claude: "open Spotify", "next song", "volume 30", "what time is it".
- Set reminders and timers.
- Voice mode: talk with Alfred like on a phone call (needs an OpenAI key).
- Telegram: text Alfred from your phone. Approvals and `/stop` work there too.
- Several PCs: "open Spotify on the laptop", "send my clipboard to the laptop".

## Staying in control

- **Approvals.** Before Alfred sends, deletes, buys or posts, runs PowerShell, or lets another of your PCs act on this one, it shows what it wants to do and waits for **Allow** or **Deny**. No answer means no.
- **STOP.** The big button is always on screen. The tray icon, Ctrl+Alt+Backspace and `/stop` in Telegram do the same. STOP ends what Alfred is doing and lets go of the mouse and keyboard.
- **Limits.** Alfred never types passwords, payment details or one-time codes, and it stops at the lock screen and admin (UAC) prompts.
- **Your data.** What you ask, text Alfred reads from your windows, and screenshots while it uses the mouse go to Anthropic (Claude). In voice mode your microphone audio goes to OpenAI. Telegram messages go through Telegram and Photon. Alfred shows you the full notice and only starts after you agree. Your settings stay on your PC, and your keys are kept in Windows Credential Manager. The notice: https://github.com/DhruvaValluru/Jarvis-Hacks/blob/v{{VERSION}}/windows-mvp/PRIVACY.md
- Alfred can make mistakes. Check anything important.

## Check your download

SHA-256 of `Alfred-Setup-{{VERSION}}.exe`: `{{SHA256}}`

In PowerShell, `Get-FileHash .\Alfred-Setup-{{VERSION}}.exe` should show the same value. It's also in `SHA256SUMS.txt`.

Built from commit {{COMMIT}} by the `alfred-desktop` workflow, which self-tested the app, installed it silently, self-tested the installed copy and uninstalled it before publishing this release. More: [install guide](https://github.com/DhruvaValluru/Jarvis-Hacks/blob/v{{VERSION}}/windows-mvp/INSTALL.md) · [using the app](https://github.com/DhruvaValluru/Jarvis-Hacks/blob/v{{VERSION}}/windows-mvp/DESKTOP.md)
