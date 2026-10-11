"""Remove what Alfred keeps for this Windows user: its settings, saved keys and logs.

  alfred-cli.exe --remove-user-data [--yes]

The uninstaller runs this (with --yes) only when you say yes to "Also remove your Alfred settings, saved
keys and activity log?" (the default is no, so a reinstall keeps them). It deletes:
  - %LOCALAPPDATA%\\Jarvis: settings.env, the data notice acceptance, the activity (audit) log, the app's
    log, the Telegram bridge's state and downloaded speech models;
  - every "Alfred/<NAME>" entry in Windows Credential Manager (API keys, bot tokens, the pairing token).
It never touches the install folder, other users or anything else on the PC. With
JARVIS_IGNORE_USER_SETTINGS=1 (the tests) it leaves Credential Manager alone.
"""

import argparse
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from . import config, settings


def saved_secret_names(backend) -> list[str]:
    """The secret names to delete: every known key, plus any other "Alfred/<NAME>" credential."""
    names = set(settings.SECRET_KEYS)
    enumerate_names = getattr(backend, "names", None)
    if enumerate_names is not None:
        names.update(enumerate_names())
    return sorted(names)


def credential_names() -> list[str]:
    """Every "Alfred/<NAME>" generic credential this user has (Windows Credential Manager)."""
    import win32cred

    try:
        creds = win32cred.CredEnumerate(settings.CRED_PREFIX + "*", 0) or []
    except Exception as e:  # pywintypes.error; ERROR_NOT_FOUND: there are none
        if settings._winerror(e) is None:
            raise
        return []
    prefix = settings.CRED_PREFIX
    return sorted(c["TargetName"][len(prefix):] for c in creds if str(c.get("TargetName", "")).startswith(prefix))


class _WinCreds(settings.WinCredBackend):
    def names(self) -> list[str]:
        return credential_names()


def remove_user_data(data_dir: Path | None = None, secrets=None, log: Callable[[str], None] = print) -> bool:
    """Delete Alfred's saved secrets and its data folder. True if everything that existed is gone."""
    ok = True
    if secrets is None:
        if os.environ.get("JARVIS_IGNORE_USER_SETTINGS") == "1":
            secrets = settings.MemoryBackend()  # tests (test.bat): never touch this PC's Credential Manager
        elif sys.platform == "win32":
            secrets = _WinCreds()
        else:
            secrets = settings.default_backend()
    try:
        names = saved_secret_names(secrets)
    except Exception as e:  # noqa: BLE001 - still delete the keys Alfred knows about
        log(f"Couldn't list Alfred's saved keys ({type(e).__name__}); removing the known ones.")
        names = sorted(settings.SECRET_KEYS)
    for name in names:
        try:
            secrets.delete(name)
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"Couldn't remove the saved key {name}: {type(e).__name__}: {e}")
    log(f"Removed Alfred's saved keys from Credential Manager ({len(names)} names checked).")

    folder = Path(data_dir) if data_dir is not None else config.data_dir()
    if folder.name.lower() != "jarvis":  # config.data_dir() is always ...\Jarvis: never delete anything else
        log(f"Not removing {folder}: it isn't Alfred's data folder.")
        return False
    if folder.exists():
        errors = []
        shutil.rmtree(folder, onexc=lambda _fn, path, exc: errors.append(f"{path}: {exc}"))
        if errors or folder.exists():
            ok = False
            log(f"Couldn't remove everything in {folder}: " + "; ".join(errors[:5]))
        else:
            log(f"Removed {folder} (settings, data notice acceptance, activity log, logs, speech models).")
    else:
        log(f"Nothing to remove at {folder}.")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="alfred-cli.exe --remove-user-data",
        description="Remove Alfred's settings, saved API keys and logs for this Windows user.")
    parser.add_argument("--yes", action="store_true", help="don't ask first (the uninstaller uses this)")
    args = parser.parse_args(argv)
    folder = config.data_dir()
    if not args.yes:
        print(f"This removes Alfred's settings, saved API keys and tokens, and logs for this Windows user:\n"
              f"  {folder}\n  Credential Manager entries named {settings.CRED_PREFIX}<NAME>")
        try:
            answer = input('Type "remove" to continue: ')
        except EOFError:
            answer = ""
        if answer.strip().lower() != "remove":
            print("Nothing removed.")
            return 1
    return 0 if remove_user_data(folder) else 1


if __name__ == "__main__":
    sys.exit(main())
