# Install Alfred (Windows app)

Alfred also comes as a normal Windows app: an installer wizard, a Start menu shortcut, and an uninstaller in Settings. No Python, no `setup.bat`, no command line. Using the app is described in [DESKTOP.md](DESKTOP.md).

## What you need

- Windows 10 version 1809 or newer, or Windows 11, 64-bit (x64).
- No administrator rights: Alfred installs for your Windows account only.
- The **Microsoft Edge WebView2 Runtime** for Alfred's own window. Windows 11 has it. If your PC doesn't, the installer offers to install it (Microsoft's installer downloads it); without it Alfred opens in your web browser instead.
- An Anthropic API key for anything beyond the simple built-in commands ([how to get one](README.md#getting-your-api-key)). You enter it in Alfred's **Settings**.

## Download the installer (test builds)

The `alfred-desktop` workflow builds the installer on GitHub Actions for every push and pull request that changes `windows-mvp/`.

1. Open the pull request on GitHub and go to its **Checks** tab (or the repository's **Actions** tab), then open the **alfred-desktop** run for the newest commit.
2. On the run's summary page, under **Artifacts**, download **Alfred-Setup** (you need to be signed in to GitHub). It's a zip file.
3. Unzip it. Inside is `Alfred-Setup-<version>.exe`.

Use the installer from a green run: that run built Alfred, self-tested it, installed it silently, self-tested the installed copy and uninstalled it again. Artifacts are kept for 30 days.

## Install

1. Double-click `Alfred-Setup-<version>.exe`.
2. **"Windows protected your PC"**: test builds aren't code-signed yet, so Microsoft Defender SmartScreen shows the publisher as **Unknown publisher**. Click **More info**, check that the app is `Alfred-Setup-<version>.exe`, and click **Run anyway**. Do this only for an installer you downloaded from this repository's Actions page. (Your browser may also say the file isn't commonly downloaded: choose **Keep**.)
3. Follow the wizard:
   - **Welcome**.
   - **What Alfred does and where your data goes**: the data notice ([PRIVACY.md](PRIVACY.md)). Read it, then choose **I have read the notice**.
   - **Where to install**: `%LOCALAPPDATA%\Programs\Alfred` unless you pick another folder.
   - **Desktop icon** if you want one (off by default).
   - **Ready**, then **Install**. If the WebView2 Runtime is missing, Setup asks whether to install it now.
   - **Finish**: with **Launch Alfred** ticked, Alfred starts.
4. The first time Alfred starts it shows the data notice again and runs only after you press **I agree**. Then add your API key under **Settings**.

**Updating:** run a newer installer. If Alfred is running, Setup says it will close it first. Your settings and keys stay.

**Silent install** (scripts and testers): `Alfred-Setup-<version>.exe /VERYSILENT /CURRENTUSER` and optionally `/DIR="D:\Apps\Alfred"` and `/LOG="setup.log"`. A silent install never installs the WebView2 Runtime.

## What goes where

| What | Where |
|---|---|
| The app: `Alfred.exe` (the window and tray), `alfred-cli.exe` (Alfred's engine and command line), `_internal\` (Python and the packages), `node\node.exe` and `telegram\` (the Telegram bridge) | `%LOCALAPPDATA%\Programs\Alfred`, or the folder you chose |
| Shortcuts | Start menu: **Alfred**. Desktop: **Alfred**, if you ticked it |
| Your settings, the data notice acceptance, the activity log, the app's log (`logs\alfred.log`) | `%LOCALAPPDATA%\Jarvis` ([DESKTOP.md](DESKTOP.md#where-settings-live)) |
| API keys and tokens | Windows Credential Manager, as `Alfred/<NAME>` |
| Uninstall entry | Settings > Apps > Installed apps (Windows 10: Apps & features) > **Alfred** |

## Check that an installed Alfred works

```bat
"%LOCALAPPDATA%\Programs\Alfred\alfred-cli.exe" --selftest
```

It checks that every part of Alfred is there and loads (on a PC without the WebView2 Runtime it skips the module for Alfred's window, because loading it there would change Internet Explorer settings), starts Alfred's engine with a throwaway data folder (none of your settings or keys, no Claude), asks it "what time is it", presses Stop, waits for a fresh engine and shuts it down. It changes nothing on your PC and ends with `RESULT: PASSED` or a list of what failed. CI runs the same check on every build.

## Uninstall

Settings > Apps > Installed apps (Windows 10: Apps & features) > **Alfred** > **Uninstall**.

- If Alfred is running, the uninstaller closes it first (and stops anything it's doing).
- It asks whether to **also remove your Alfred settings, saved API keys and activity log**. The answer is **No** unless you pick Yes, so a later reinstall finds everything as you left it. Yes deletes `%LOCALAPPDATA%\Jarvis` and Alfred's `Alfred/<NAME>` entries in Credential Manager.
- Silent: `unins000.exe /VERYSILENT` in Alfred's folder keeps your data; add `/REMOVEUSERDATA` to delete it too.
- To delete your settings, keys and logs without uninstalling: `alfred-cli.exe --remove-user-data`.

## Build it yourself

You need Windows x64, Python 3.12 (`setup.bat` sets it up) and Node.js 22. `fetch_innosetup.ps1` gets the Inno Setup version CI uses: it needs no administrator rights and only unpacks it into `build\innosetup`. (Without that, `installer.ps1` uses an installed [Inno Setup 6.3 or newer](https://jrsoftware.org/isinfo.php).)

```bat
cd windows-mvp
setup.bat
.venv\Scripts\python -m pip install -r requirements.lock.txt -r packaging\requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python .venv\Scripts\python.exe
dist\Alfred\alfred-cli.exe --selftest
powershell -ExecutionPolicy Bypass -File packaging\fetch_innosetup.ps1
powershell -ExecutionPolicy Bypass -File packaging\installer.ps1
powershell -ExecutionPolicy Bypass -File packaging\check_installer.ps1
```

The last line is optional: it installs the new `dist\Alfred-Setup-<version>.exe` silently into a temporary folder, self-tests that copy and uninstalls it, as CI does. It keeps your Alfred settings and keys, and refuses to run on a PC where Alfred is installed (every install shares one uninstall entry); use Windows Sandbox there.

| File (in `packaging\`) | What it does |
|---|---|
| `build.ps1` | Builds `dist\Alfred`: checks the packages match the spec (`--doctor`), draws the icon, runs PyInstaller, adds Node.js and the Telegram bridge |
| `alfred.spec` | PyInstaller: `Alfred.exe` (windowed) and `alfred-cli.exe` (console) sharing one `_internal` folder, with the data files and DLLs Alfred loads by path |
| `alfred_app.py`, `alfred_cli.py` | The two programs' entry points |
| `make_icon.py` | Draws the icon and the installer's pictures in code |
| `fetch_node.ps1` | Downloads the pinned official Node.js for Windows and checks its SHA-256 against nodejs.org's `SHASUMS256.txt` and the hash pinned in the script |
| `fetch_innosetup.ps1` | Downloads the pinned Inno Setup from its official GitHub release, checks its SHA-256 against the hash pinned in the script, and unpacks it in portable mode into `build\innosetup` |
| `installer.ps1`, `alfred.iss` | Inno Setup: `dist\Alfred-Setup-<version>.exe` (it also fetches Microsoft's WebView2 bootstrapper and checks its signature) |
| `check_installer.ps1` | Silent install, self-test, silent uninstall |
| `requirements-build.txt` | The exact PyInstaller version |

What's pinned: Python 3.12.10 and `requirements.lock.txt` (the [test spec](TESTING.md)), PyInstaller (`requirements-build.txt`), Node.js 22.23.3 (`fetch_node.ps1`, the same version CI tests with) and Inno Setup 6.7.3 (`fetch_innosetup.ps1`; CI always compiles with it, never with the Inno Setup the runner image happens to have; ISCC prints its version in the log).

## Code signing

Test builds aren't signed, which is why SmartScreen says "Unknown publisher" and some antivirus tools look more closely at them. Before installers go to people outside the team, `Alfred.exe`, `alfred-cli.exe`, the installer and its uninstaller should be signed (Inno Setup's `SignTool` setting covers the last two). Which certificate to use is the repository owner's decision (task K3).
