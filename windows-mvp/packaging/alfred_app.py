"""Alfred.exe: the desktop app (jarvis.app.main), built windowed so no console window opens.

A windowed exe has no stdout or stderr, so what the app prints (and any error trace) goes to
%LOCALAPPDATA%\\Jarvis\\logs\\alfred.log instead; the previous run's log is kept as alfred.log.1.
"""

import contextlib
import sys


def _log_to_file() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return  # started from a console (or by a debugger): keep printing there
    try:
        from jarvis import config

        folder = config.data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "alfred.log"
        with contextlib.suppress(OSError):  # in use: Alfred is already running, and this copy will just show it
            path.replace(path.with_name("alfred.log.1"))
        log = open(path, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115 - for the app's life
    except OSError:
        return  # no log then: print() without a console does nothing
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


if __name__ == "__main__":
    _log_to_file()
    from jarvis.app.main import main

    sys.exit(main())
