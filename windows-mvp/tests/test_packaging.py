"""The Windows app bundle and installer (windows-mvp/packaging, built by .github/workflows/alfred-desktop.yml):
what can be checked without Windows. CI builds, self-tests, installs and uninstalls the real thing."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

MVP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MVP))

from jarvis import config, settings, uninstall  # noqa: E402
from jarvis.service import bridge_command, engine_command  # noqa: E402
from jarvis.settings import MemoryBackend  # noqa: E402

PACKAGING = MVP / "packaging"
WORKFLOWS = MVP.parent / ".github" / "workflows"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"packaging_{name}", PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def frozen_app(tmp_path, monkeypatch):
    """Pretend to be the installed app: sys.executable is <app>\\Alfred.exe."""
    app = tmp_path / "Alfred"
    app.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Alfred.exe"))
    return app


def test_the_installed_app_starts_its_engine_and_bridge_from_the_bundle(frozen_app):
    assert config.frozen() and config.app_dir() == frozen_app.resolve()
    assert engine_command() == [str(frozen_app.resolve() / "alfred-cli.exe"), "--engine"]
    assert bridge_command() == ["node", str(frozen_app.resolve() / "telegram" / "index.mjs")]  # no node.exe yet
    (frozen_app / "node").mkdir()
    (frozen_app / "node" / "node.exe").write_bytes(b"MZ")
    assert bridge_command() == [str(frozen_app.resolve() / "node" / "node.exe"),
                                str(frozen_app.resolve() / "telegram" / "index.mjs")]


def test_cli_entry_dispatches(monkeypatch):
    cli = _load("alfred_cli")
    seen = []
    monkeypatch.setattr("jarvis.engine.main", lambda: seen.append("engine") or 0)
    monkeypatch.setattr("jarvis.selftest.main", lambda argv: seen.append(("selftest", argv)) or 1)
    monkeypatch.setattr("jarvis.selftest.exit_now", lambda code: code)  # really: os._exit(code)
    monkeypatch.setattr("jarvis.uninstall.main", lambda argv: seen.append(("remove", argv)) or 0)
    monkeypatch.setattr("jarvis.__main__.main", lambda: seen.append("cli"))
    for argv, result in ((["--engine"], 0), (["--selftest", "--help"], 1), (["--remove-user-data", "--yes"], 0),
                         (["--list-tools"], None), ([], None)):
        monkeypatch.setattr(sys, "argv", ["alfred-cli.exe", *argv])
        assert cli.main() == result
    assert seen == ["engine", ("selftest", ["--help"]), ("remove", ["--yes"]), "cli", "cli"]


def test_windowed_app_logs_to_a_file(tmp_path, monkeypatch):
    app = _load("alfred_app")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = tmp_path / "Jarvis" / "logs" / "alfred.log"
    log.parent.mkdir(parents=True)
    log.write_text("last run\n")
    app._log_to_file()
    try:
        print("Alfred is closing...")
        assert sys.stdout is sys.stderr
    finally:
        sys.stdout.close()
    assert log.read_text() == "Alfred is closing...\n"
    assert (log.parent / "alfred.log.1").read_text() == "last run\n"


def test_remove_user_data(tmp_path):
    folder = tmp_path / "Jarvis"
    (folder / "logs").mkdir(parents=True)
    (folder / "settings.env").write_text("JARVIS_NAME=desk\n")
    (folder / "logs" / "alfred.log").write_text("x")
    secrets = MemoryBackend({"ANTHROPIC_API_KEY": "sk-ant-1", "JARVIS_TOKEN": "t", "OLD_KEY": "o"})
    secrets.names = lambda: ["OLD_KEY"]  # what Credential Manager lists under Alfred/*
    lines = []
    assert uninstall.remove_user_data(folder, secrets, log=lines.append)
    assert not folder.exists()
    assert all(secrets.get(key) is None for key in ("ANTHROPIC_API_KEY", "JARVIS_TOKEN", "OLD_KEY"))
    assert any("Removed" in line and str(folder) in line for line in lines)
    assert uninstall.remove_user_data(folder, MemoryBackend(), log=lines.append)  # nothing left: still fine


def test_remove_user_data_never_deletes_another_folder(tmp_path):
    other = tmp_path / "Documents"
    other.mkdir()
    (other / "keep.txt").write_text("mine")
    assert not uninstall.remove_user_data(other, MemoryBackend(), log=lambda line: None)
    assert (other / "keep.txt").exists()


def test_remove_user_data_asks_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("JARVIS_IGNORE_USER_SETTINGS", "1")  # never this PC's Credential Manager
    monkeypatch.setattr(uninstall, "_WinCreds", None)  # (and fail loudly if it were used anyway)
    settings_file = config.data_dir() / "settings.env"
    settings_file.write_text("JARVIS_NAME=desk\n")
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert uninstall.main([]) == 1 and settings_file.exists()
    assert "Nothing removed." in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt: "remove")
    assert uninstall.main([]) == 0 and not settings_file.exists()
    settings_file = config.data_dir() / "settings.env"
    settings_file.write_text("JARVIS_NAME=desk\n")
    assert uninstall.main(["--yes"]) == 0 and not settings_file.exists()


def test_the_uninstaller_fallback_knows_every_saved_key():
    iss = (PACKAGING / "alfred.iss").read_text(encoding="utf-8")
    listed = re.search(r"SecretKeys = '([^']*)'", iss)[1].split(",")
    assert sorted(listed) == sorted(settings.SECRET_KEYS)
    assert settings.CRED_PREFIX == "Alfred/" and "'/delete:Alfred/' + Key" in iss
    assert "{localappdata}\\Jarvis" in iss and config.data_dir().name == "Jarvis"


def test_installer_and_ci_pin_the_same_versions():
    desktop = (WORKFLOWS / "alfred-desktop.yml").read_text(encoding="utf-8")
    spec_ci = (WORKFLOWS / "windows-mvp.yml").read_text(encoding="utf-8")
    fetch = (PACKAGING / "fetch_node.ps1").read_text(encoding="utf-8")
    node = re.search(r"\[string\]\$Version = '(22\.\d+\.\d+)'", fetch)[1]
    assert re.search(r'NODE_VERSION: "([\d.]+)"', desktop)[1] == node
    assert re.search(r'node-version: "([\d.]+)"', spec_ci)[1] == node
    assert re.search(r"\[string\]\$Sha256 = '([0-9a-f]{64})'", fetch)
    assert re.search(r'PYTHON_VERSION: "([\d.]+)"', desktop)[1] == re.search(r'python-version: "([\d.]+)"', spec_ci)[1]
    assert "runs-on: windows-2025" in desktop and "name: Alfred-Setup" in desktop
    build = (PACKAGING / "requirements-build.txt").read_text()
    assert re.search(r"^pyinstaller==\d", build, re.M) and re.search(r"^pyinstaller-hooks-contrib==\d", build, re.M)
    assert all("==" in line for line in build.splitlines() if line and not line.startswith("#"))


def test_the_spec_and_entry_scripts_fit_together():
    spec = (PACKAGING / "alfred.spec").read_text(encoding="utf-8")
    compile(spec, "alfred.spec", "exec")  # at least valid Python
    for entry in ("alfred_app.py", "alfred_cli.py"):
        assert entry in spec and (PACKAGING / entry).is_file()
    assert 'name="Alfred"' in spec and 'name="alfred-cli"' in spec  # what engine_command() and the .iss expect
    iss = (PACKAGING / "alfred.iss").read_text(encoding="utf-8")
    assert '#define AppExe "Alfred.exe"' in iss and '#define CliExe "alfred-cli.exe"' in iss
    for data in ("PRIVACY.md", "requirements.lock.txt", '"jarvis" / "app" / "ui"'):
        assert data in spec


def test_icon_and_installer_art(tmp_path):
    from PIL import Image

    make_icon = _load("make_icon")
    assert make_icon.main([str(tmp_path)]) == 0
    with Image.open(tmp_path / "alfred.ico") as ico:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])
    with Image.open(tmp_path / "alfred.png") as png:
        assert png.size == (256, 256) and png.mode == "RGBA"
        assert png.getpixel((0, 0))[3] == 0 and png.getpixel((128, 128))[3] == 255  # a disc on transparency
    for name, size in (("wizard-large-100.bmp", (164, 314)), ("wizard-large-200.bmp", (328, 628)),
                       ("wizard-small-100.bmp", (55, 55)), ("wizard-small-200.bmp", (110, 110))):
        with Image.open(tmp_path / name) as bmp:
            assert bmp.size == size and bmp.mode == "RGB", name
