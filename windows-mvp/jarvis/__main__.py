"""Run Jarvis.

  python -m jarvis              talk or type (press Enter on an empty line to speak)
  python -m jarvis --text       typing only, no microphone
  python -m jarvis --wake       hands-free: say "Jarvis, ..." any time
  python -m jarvis --do "open notepad"   run one command and exit
  python -m jarvis --serve       let other PCs use this PC's tools (prints pairing info)
  python -m jarvis --list-tools
  python -m jarvis --doctor     check this PC matches the shared test spec

Multi-PC: put the same JARVIS_TOKEN in .env on each PC (see jarvis/remote.py), then
"open spotify on the laptop" or "copy report.docx from the laptop to here and open it".
"""

import argparse
import re
import sys

from . import config, remote, router, tools
from .tools import registry


def console_confirm(reason: str) -> bool:
    try:
        answer = input(f"\n  Jarvis wants to: {reason}\n  Allow? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


class Jarvis:
    def __init__(self, cfg: config.Config, speaker=None, *, on_reply=None, on_confirm=None):
        self.cfg = cfg
        self.speaker = speaker
        self.on_reply = on_reply
        self.on_confirm = on_confirm
        self.brain = None
        if cfg.has_llm:
            from .brain import Brain

            self.brain = Brain(cfg, confirm=self.confirm, status=lambda s: print(f"  {s}"))
        registry.announce = self.reply

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
        print(f"Jarvis: {text}")
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
            self.reply("I only know simple commands without an API key. Add ANTHROPIC_API_KEY to windows-mvp\\.env.")
            return
        print("  thinking...")
        self.reply(self.brain.ask(text))


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--text", action="store_true", help="typing only, no microphone")
    p.add_argument("--wake", action="store_true", help='hands-free: listens for "Jarvis"')
    p.add_argument("--do", metavar="COMMAND", help="run one command and exit")
    p.add_argument("--quiet", action="store_true", help="don't speak replies")
    p.add_argument("--serve", action="store_true", help="only serve this PC's tools to other PCs")
    p.add_argument("--list-tools", action="store_true")
    p.add_argument("--doctor", action="store_true", help="check this PC matches the test spec")
    args = p.parse_args()

    if args.doctor:
        from . import doctor

        sys.exit(doctor.main())

    cfg = config.load()
    if args.list_tools:
        for t in tools.TOOLS.values():
            if t.hidden:
                continue
            print(f"{t.name:18} [{t.risk:7}] {t.description.splitlines()[0][:90]}")
        state = "on" if cfg.computer_use else "off (JARVIS_COMPUTER_USE=0)"
        print(f"{'computer':18} [toolset] screenshot, click, type, key, scroll, zoom ... : {state}")
        return

    if sys.platform == "win32":
        from .tools.wininput import enable_dpi_awareness

        enable_dpi_awareness()  # before any window or screen code, so screenshots and clicks line up
    from .voice import Speaker

    speaker = Speaker(enabled=cfg.speak and not args.quiet)
    registry.speak = lambda text: speaker.say(text) if speaker.enabled else print(f"(says) {text}")
    if args.serve:
        print("=" * 60)
        print(" JARVIS - serving this PC to your other PCs. Ctrl+C to stop.")
        print("=" * 60)
        try:
            remote.serve(cfg)
        except KeyboardInterrupt:
            print("\nBye.")
        return
    if cfg.serve:
        remote.serve(cfg, log_fn=lambda s: print(f" {s}"), background=True)
    remote.connect(cfg, log_fn=lambda s: print(f" {s}"))
    jarvis = Jarvis(cfg, speaker)

    if args.do:
        jarvis.handle(args.do)
        speaker.wait()
        return

    print("=" * 60)
    print(" JARVIS - Windows MVP")
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
    jarvis.reply("Jarvis online.")

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
    print('Listening. Start with "Jarvis", e.g. "Jarvis, open Spotify".')
    while True:
        speaker.wait()
        heard = listener.listen(max_wait=3600, max_len=15)
        if not re.search(r"\bjarvis\b", heard, re.I):
            continue
        command = re.split(r"\bjarvis\b[\s,.!?]*", heard, maxsplit=1, flags=re.I)[1].strip()
        print(f"You said: {heard}")
        if not command:
            jarvis.reply("Yes?")
            speaker.wait()
            command = listener.listen()
            print(f"You said: {command or '(nothing)'}")
        jarvis.handle(command)


if __name__ == "__main__":
    main()
