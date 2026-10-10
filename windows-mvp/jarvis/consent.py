"""Data and terms notice that every user accepts before Alfred runs (TASKS F8).

The full text is PRIVACY.md. Acceptance is stored per user in
%LOCALAPPDATA%\\Jarvis\\consent.json with the notice version; changing what data
goes where means bumping TERMS_VERSION, so everyone sees and accepts it again.
"""

import json
import os
import time

from . import config

TERMS_VERSION = "2026-10-10"

NOTICE = """\
================ Alfred: what it does and where your data goes ================
Alfred runs on this PC as you. It can open apps, read and type into them, and
move your mouse and keyboard (computer use), including when you text it from
Telegram. It asks before sending, deleting, buying or posting, never types
passwords, and can make mistakes: check anything important.

Your data goes to:
 - Anthropic (Claude API): what you ask, text Alfred reads from your windows and
   documents, and screenshots of your screen when it uses computer use or you
   ask it to look.
 - OpenAI, only if OPENAI_API_KEY is set: Telegram voice notes (to transcribe
   them) and reply text (to make spoken replies).
 - Telegram and Photon Spectrum, only if you set up Telegram: your messages and
   Alfred's replies pass through their servers.
Stays on this PC: microphone audio (speech is recognised locally), the spoken
voice, and the action log at %LOCALAPPDATA%\\Jarvis\\audit.jsonl.

Stop it any time: Ctrl+C, move the mouse to the top-left corner, or send /stop
in Telegram. Full notice: windows-mvp\\PRIVACY.md (version {version}).
==============================================================================="""


def _path():
    return config.data_dir() / "consent.json"


def accepted() -> bool:
    if os.environ.get("JARVIS_ACCEPT_TERMS") == TERMS_VERSION:
        return True  # unattended installs and automated tests, set on purpose by whoever runs them
    try:
        return json.loads(_path().read_text(encoding="utf-8")).get("version") == TERMS_VERSION
    except (OSError, ValueError, AttributeError):
        return False


def record(source: str = "app") -> None:
    """Store that the current notice was accepted, and where (the app, the console, setup)."""
    _path().write_text(
        json.dumps({"version": TERMS_VERSION, "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source}),
        encoding="utf-8",
    )


def notice_markdown() -> str:
    """The full notice (PRIVACY.md) for the app to show; the short console NOTICE if it's missing."""
    for folder in (config.MVP_DIR, config.app_dir()):
        try:
            return (folder / "PRIVACY.md").read_text(encoding="utf-8")
        except OSError:
            continue
    return NOTICE.format(version=TERMS_VERSION)


def ensure(interactive: bool) -> bool:
    """True if the current notice is accepted. Asks once when someone is at the keyboard."""
    if accepted():
        return True
    if not interactive:
        return False
    print(NOTICE.format(version=TERMS_VERSION))
    try:
        answer = input('Type "agree" to accept and continue: ')
    except EOFError:
        return False
    if answer.strip().lower() == "agree":
        record("console")
        print("Thanks. You can read the full notice again in windows-mvp\\PRIVACY.md.")
        return True
    print("Not accepted. Alfred won't run until you accept (run jarvis.bat --terms).")
    return False


NOT_ACCEPTED = "Alfred's data notice hasn't been accepted on this PC yet. At the PC, run: jarvis.bat --terms"
