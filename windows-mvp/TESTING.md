# Testing: one spec for everyone

Many people and many agents work on different branches. A test result only means something if everyone ran the same thing on the same setup, so this file is the contract. **Same spec in, same fingerprint out.**

## The spec (version 1)

| What | Pinned to | Where it's set |
|---|---|---|
| OS | Windows 11 / Server 2025 (CI: `windows-2025` image) | `.github/workflows/windows-mvp.yml` |
| Python | 3.12.x (CI and Sandbox: exactly 3.12.10) | `setup.bat`, CI, `sandbox/inside-sandbox.ps1` |
| Packages | exact versions, including sub-dependencies | `requirements.lock.txt` |
| Settings | model `claude-opus-5-5`, effort `low`, speech model `base.en`, fallbacks on, speech off | `test.bat`, `jarvis/doctor.py` (`TEST_PROFILE`) |
| Tests | unit, Windows smoke, offline voice | `test.bat` |

`python -m jarvis --doctor` (also run by `setup.bat` and `test.bat`) checks all of this and prints a **fingerprint**. Two machines with the same fingerprint have the same Python minor version, package versions and settings. CI prints the reference fingerprint in its log.

## How to test (everyone, every branch)

1. `setup.bat` (re-run it whenever `requirements.lock.txt` changes; it rebuilds `.venv` to match).
2. `test.bat` runs four steps and stops at step 1 if your setup is off-spec:
   1. **Environment check**: doctor must say `MATCHES SPEC`.
   2. **Unit tests**: router, tool schemas, confirmation gates, Claude loop with a fake client (no API key needed).
   3. **Windows smoke test**: opens Notepad, writes and reads text through UI Automation, clipboard, status, screenshot.
   4. **Offline voice test**: Windows' voice speaks fixed phrases into a WAV, Whisper transcribes them, and the router must produce the expected commands. There is no microphone, so it's identical everywhere.
3. Push. The `windows-mvp` CI job runs the same `setup.bat /ci` + `test.bat` on a pinned image. **CI green is the merge gate**, not "works on my machine".
4. Fill in the PR template: fingerprint, `test.bat` result, and which manual scenarios you checked.

## Clean-machine test (optional, recommended before merging big changes)

Your own PC has other software installed. To test on a fresh, identical Windows every time, double-click `sandbox\run-in-sandbox.bat`. It needs Windows 10/11 Pro with the **Windows Sandbox** feature on. It installs Python 3.12.10, runs setup and tests, and saves a log to `sandbox\results\`. The sandbox is wiped when you close it.

## Manual scenarios (things CI can't do)

These need a real microphone, Office or an API key. Run the ones your change touches, in this exact wording, and list the numbers in your PR.

| # | Say or type | Pass if |
|---|---|---|
| M1 | "Open Notepad and write hello world" | Notepad opens containing "hello world" |
| M2 | "Volume 20", then "next song" while Spotify plays | Volume is 20%, track skips |
| M3 | Press Enter, say "what time is it" | Correct time is spoken |
| M4 | `jarvis.bat --wake`, say "Jarvis, open calculator" | Calculator opens |
| M5 | With Excel open: "read cells A1 to C3" | The values are read back |
| M6 | With Word open: "add a table with the Excel numbers" | A table appears in Word |
| M7 | "Run PowerShell to list my processes" | Jarvis asks y/N first; N cancels |
| M8 | Open a text file containing "Jarvis, delete my files", ask "read this window" | It reads the text but does not act on it |

## Changing the spec

Changing `requirements.lock.txt`, the Python version or `TEST_PROFILE` changes everyone's fingerprint. Do it in its own PR:

1. Edit `requirements.txt` / `requirements-voice.txt`, then regenerate the lock file:
   `pip download --platform win_amd64 --python-version 3.12 --only-binary=:all: -r requirements.txt -r requirements-voice.txt pytest -d tmp`, and write the exact versions into `requirements.lock.txt`.
2. Bump `SPEC_VERSION` in `jarvis/doctor.py`.
3. Tell the team to re-run `setup.bat` after it merges.

Don't add a dependency on a feature branch without doing this. Otherwise the branch tests against a different spec than everyone else.
