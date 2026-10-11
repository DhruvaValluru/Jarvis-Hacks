"""Run Alfred (the code and commands are still named jarvis).

  python -m jarvis              talk or type (press Enter on an empty line to speak)
  python -m jarvis --text       typing only, no microphone
  python -m jarvis --wake       hands-free: say "Alfred, ..." any time
  python -m jarvis --do "open notepad"   run one command and exit
  python -m jarvis --serve       let other PCs use this PC's tools (prints pairing info)
  python -m jarvis --show-token  print this PC's JARVIS_TOKEN to pair another PC
  python -m jarvis --list-tools
  python -m jarvis --doctor     check this PC matches the shared test spec
  python -m jarvis --terms      show the data notice and accept it (setup.bat runs this)

Multi-PC: put the same JARVIS_TOKEN in .env on each PC (see jarvis/remote.py), then
"open spotify on the laptop" or "copy report.docx from the laptop to here and open it".
"""

import argparse
import re
import sys
import threading

from . import config, consent, remote, router, tools
from .tools import registry


def console_confirm(reason: str) -> bool:
    try:
        answer = input(f"\n  Alfred wants to: {reason}\n  Allow? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


_ask_lock = threading.Lock()


def local_confirm(reason: str, timeout: int = 60) -> bool:
    """Another PC wants to do something consequential here: ask whoever sits at this PC, one at a time.
    On Windows a dialog, since the console may be waiting for the next command; no answer means no."""
    with _ask_lock:
        if sys.platform != "win32":
            return console_confirm(reason)
        import ctypes

        user32 = ctypes.windll.user32
        text = f"Another of your PCs asks Alfred to:\n\n{reason}\n\nAllow it on this PC? (No after {timeout} s.)"
        flags = 0x4 | 0x30 | 0x100 | 0x1000 | 0x10000 | 0x40000  # Yes/No, warning, No first, on top of everything
        try:
            answer = user32.MessageBoxTimeoutW(None, text, "Alfred", flags, 0, timeout * 1000)
        except AttributeError:
            answer = user32.MessageBoxW(None, text, "Alfred", flags)
        return answer == 6  # IDYES


class Jarvis:
    def __init__(self, cfg: config.Config, speaker=None, *, on_reply=None, on_confirm=None, on_status=None):
        self.cfg = cfg
        self.speaker = speaker
        self.on_reply = on_reply
        self.on_confirm = on_confirm
        self.on_status = on_status  # on_status(kind, text); kind is "thinking", "tool" or "text"
        self.brain = None
        if cfg.has_llm:
            from .brain import Brain

            self.brain = Brain(cfg, confirm=self.confirm, status=self._brain_status)
        registry.announce = self.reply

    def status(self, kind: str, text: str) -> None:
        """Progress while a request runs. The console prints it; apps get it through on_status."""
        if self.on_status:
            self.on_status(kind, text.strip())
        else:
            print(f"  {text}")

    def _brain_status(self, text: str) -> None:
        self.status("tool" if text.lstrip().startswith("->") else "text", text)

    def confirm(self, reason: str) -> bool:
        if self.on_confirm:
            return self.on_confirm(reason)
        if self.speaker:
            self.speaker.say("Should I go ahead?")
        return console_confirm(reason)

    def reply(self, text: str) -> None:
        if self.on_reply:
            self.on_reply(text)
            return
        print(f"Alfred: {text}")
        if self.speaker:
            self.speaker.say(text)

    def handle(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if router.clean(text).lower() in ("new conversation", "forget that", "reset"):
            if self.brain:
                self.brain.reset()
            self.reply("Okay, fresh start.")
            return
        pc, rest = remote.split_target(router.clean(text)) if remote.DEVICES else (None, text)
        fast = router.route(rest)
        if fast and pc and pc != remote.here:
            name = f"{pc}{remote.SEP}{fast[0]}"
            fast = (name, fast[1]) if name in tools.TOOLS else None  # offline: let Claude explain
        if fast:
            name, args = fast
            ok, out = tools.run(name, args, self.confirm)
            if ok or not self.brain:
                self.reply(str(out))
                return
        if not self.brain:
            where = "ANTHROPIC_API_KEY to windows-mvp\\.env"
            if config.frozen():
                where = "your Anthropic API key in Alfred settings"
            self.reply(f"I only know simple commands without an API key. Add {where}.")
            return
        self.status("thinking", "thinking...")
        self.reply(self.brain.ask(text))


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--text", action="store_true", help="typing only, no microphone")
    p.add_argument("--wake", action="store_true", help='hands-free: listens for "Alfred"')
    p.add_argument("--do", metavar="COMMAND", help="run one command and exit")
    p.add_argument("--quiet", action="store_true", help="don't speak replies")
    p.add_argument("--serve", action="store_true", help="only serve this PC's tools to other PCs")
    p.add_argument("--show-token", action="store_true", help="print this PC's JARVIS_TOKEN to pair another PC")
    p.add_argument("--list-tools", action="store_true")
    p.add_argument("--doctor", action="store_true", help="check this PC matches the test spec")
    p.add_argument("--terms", action="store_true", help="show the data notice and accept it")
    p.add_argument("--engine", action="store_true", help=argparse.SUPPRESS)  # the app's engine (engine.py)
    # The setup's speech model download, when installed (onboarding/speech.py: download_command)
    p.add_argument("--fetch-speech-model", nargs=2, metavar=("MODEL", "FOLDER"), help=argparse.SUPPRESS)
    args = p.parse_args()

    if args.engine:
        from . import engine

        sys.exit(engine.main())
    if args.fetch_speech_model:
        from .onboarding import speech

        speech.run_and_exit(args.fetch_speech_model)

    if args.terms:
        if consent.accepted():
            print(consent.NOTICE.format(version=consent.TERMS_VERSION))
            print("Already accepted on this PC.")
            sys.exit(0)
        sys.exit(0 if consent.ensure(interactive=True) else 1)

    if args.doctor:
        from . import doctor

        sys.exit(doctor.main())

    cfg = config.load()
    if args.show_token:
        print(f"JARVIS_TOKEN={cfg.token or remote.ensure_token()}")
        print(f"Put this line in {config.settings_location()} on your other PCs. Keep it secret: it controls this PC.")
        return
    if args.list_tools:
        for t in tools.TOOLS.values():
            if t.hidden:
                continue
            print(f"{t.name:18} [{t.risk:7}] {t.description.splitlines()[0][:90]}")
        state = "on" if cfg.computer_use else "off (JARVIS_COMPUTER_USE=0)"
        print(f"{'computer':18} [toolset] screenshot, click, type, key, scroll, zoom ... : {state}")
        return

    if not consent.ensure(interactive=sys.stdin.isatty()):
        print(consent.NOT_ACCEPTED)
        sys.exit(1)

    if sys.platform == "win32":
        from .tools.wininput import enable_dpi_awareness

        enable_dpi_awareness()  # before any window or screen code, so screenshots and clicks line up
    from .voice import Speaker

    speaker = Speaker(enabled=cfg.speak and not args.quiet)
    registry.speak = lambda text: speaker.say(text) if speaker.enabled else print(f"(says) {text}")
    if args.serve or cfg.serve:
        remote.set_local_confirm(local_confirm)  # what other PCs want to do here is approved here
    if args.serve:
        print("=" * 60)
        print(" ALFRED - serving this PC to your other PCs. Ctrl+C to stop.")
        print("=" * 60)
        try:
            remote.serve(cfg)
        except KeyboardInterrupt:
            print("\nBye.")
        except (OSError, ValueError) as e:
            print(f" Couldn't share this PC. {e}")
            sys.exit(1)
        return
    if cfg.serve:
        try:
            remote.serve(cfg, log_fn=lambda s: print(f" {s}"), background=True)
        except (OSError, ValueError) as e:
            print(f" Couldn't share this PC with your other PCs. {e}")
    remote.connect(cfg, log_fn=lambda s: print(f" {s}"))
    jarvis = Jarvis(cfg, speaker)

    if args.do:
        jarvis.handle(args.do)
        speaker.wait()
        return

    print("=" * 60)
    print(" ALFRED - Windows MVP")
    print(f" Brain: {cfg.model} (effort {cfg.effort})" if cfg.has_llm else " Brain: none (no API key) - simple commands only")
    if cfg.has_llm and cfg.computer_use:
        print(" Computer use: on. Move the mouse to the top-left corner to take it back.")
    listener = None
    if not args.text:
        try:
            from .voice import Listener

            listener = Listener(cfg.whisper_model)
        except Exception as e:  # noqa: BLE001
            print(f" Voice input unavailable ({type(e).__name__}: {e}). Typing still works.")
            if args.wake:
                sys.exit(1)
    print(" Ctrl+C to quit. Audit log:", config.data_dir() / "audit.jsonl")
    print("=" * 60)
    jarvis.reply("Alfred online.")

    try:
        if args.wake and listener:
            wake_loop(jarvis, listener, speaker)
        else:
            chat_loop(jarvis, listener, speaker)
    except KeyboardInterrupt:
        print("\nBye.")


def chat_loop(jarvis: Jarvis, listener, speaker) -> None:
    hint = "Press Enter and speak, or type" if listener else "Type"
    while True:
        speaker.wait()
        try:
            line = input(f"\n[{hint}] > ")
        except EOFError:
            return
        if line.strip().lower() in ("exit", "quit"):
            return
        if not line.strip() and listener:
            print("  listening...")
            line = listener.listen()
            print(f"You said: {line or '(nothing)'}")
        jarvis.handle(line)


def wake_loop(jarvis: Jarvis, listener, speaker) -> None:
    print('Listening. Start with "Alfred", e.g. "Alfred, open Spotify".')
    while True:
        speaker.wait()
        heard = listener.listen(max_wait=3600, max_len=15)
        if not re.search(r"\b(alfred|jarvis)\b", heard, re.I):
            continue
        command = re.split(r"\b(?:alfred|jarvis)\b[\s,.!?]*", heard, maxsplit=1, flags=re.I)[1].strip()
        print(f"You said: {heard}")
        if not command:
            jarvis.reply("Yes?")
            speaker.wait()
            command = listener.listen()
            print(f"You said: {command or '(nothing)'}")
        jarvis.handle(command)


if __name__ == "__main__":
    main()
