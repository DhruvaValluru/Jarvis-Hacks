"""alfred-cli.exe: the installed app's console program.

  alfred-cli.exe --engine              the engine process the app starts (jarvis.service.engine_command)
  alfred-cli.exe --selftest            check this copy of Alfred is complete and works (jarvis/selftest.py)
  alfred-cli.exe --remove-user-data    remove your settings, saved keys and logs (jarvis/uninstall.py;
                                       the uninstaller runs it with --yes when you ask it to)
  alfred-cli.exe [options]             the jarvis command line, as python -m jarvis (--help lists the options)
"""

import sys


def main():
    command, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if command == "--engine":
        from jarvis import engine

        return engine.main()
    if command == "--selftest":
        from jarvis import selftest

        return selftest.exit_now(selftest.main(rest))
    if command == "--remove-user-data":
        from jarvis import uninstall

        return uninstall.main(rest)
    from jarvis.__main__ import main as cli

    return cli()


if __name__ == "__main__":
    sys.exit(main())
