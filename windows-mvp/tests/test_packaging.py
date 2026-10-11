"""The Windows app bundle and installer (windows-mvp/packaging, built by .github/workflows/alfred-desktop.yml):
what can be checked without Windows. CI builds, self-tests, installs and uninstalls the real thing."""

import base64
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MVP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MVP))

from jarvis import __version__, config, settings, uninstall  # noqa: E402
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


def test_ci_compiles_with_the_pinned_inno_setup():
    """The same commit must always give the same installer: CI never takes whatever Inno Setup the runner
    image or Chocolatey has today, only the version and hash pinned in fetch_innosetup.ps1."""
    desktop = (WORKFLOWS / "alfred-desktop.yml").read_text(encoding="utf-8")
    fetch = (PACKAGING / "fetch_innosetup.ps1").read_text(encoding="utf-8")
    version = re.search(r"\[string\]\$Version = '(\d+)\.(\d+)\.(\d+)'", fetch)
    assert version and re.search(r"\[string\]\$Sha256 = '[0-9a-f]{64}'", fetch)
    assert "github.com/jrsoftware/issrc/releases/download/" in fetch and "/PORTABLE=1" in fetch
    assert "choco" not in desktop
    run = re.findall(r"run: (.*)", desktop)
    fetch_step = run.index("./packaging/fetch_innosetup.ps1")
    assert run[fetch_step + 1] == "./packaging/installer.ps1 -Iscc build\\innosetup\\ISCC.exe"
    iss = (PACKAGING / "alfred.iss").read_text(encoding="utf-8")
    floor = tuple(int(n) for n in re.search(r"#if VER < EncodeVer\((\d+), (\d+), (\d+), 0\)", iss).groups())
    assert tuple(int(n) for n in version.groups()) >= floor
    pinned = ".".join(version.groups())
    assert f"Inno Setup {pinned}" in (MVP / "INSTALL.md").read_text(encoding="utf-8")
    assert "Join-Path $Build 'innosetup\\ISCC.exe'" in (PACKAGING / "installer.ps1").read_text(encoding="utf-8")


def test_a_release_publishes_the_installer_ci_checked():
    """Run workflow with "release" ticked publishes the installer the installer job just built and tested
    (INSTALL.md, "Download a release"). Only that job may write to the repository, it finds the version where
    installer.ps1 does, and the release notes have a place for everything the job fills in."""
    yaml = pytest.importorskip("yaml")
    desktop = yaml.safe_load((WORKFLOWS / "alfred-desktop.yml").read_text(encoding="utf-8"))
    inputs = desktop[True]["workflow_dispatch"]["inputs"]  # PyYAML reads the key `on` as True
    assert inputs["release"]["type"] == "boolean" and inputs["release"]["default"] is False
    assert desktop["permissions"] == {"contents": "read"}
    release = desktop["jobs"]["release"]
    assert release["needs"] == "installer" and release["permissions"] == {"contents": "write"}
    assert release["if"] == "github.event_name == 'workflow_dispatch' && inputs.release"

    def uses(job, action):
        return [s["with"] for s in desktop["jobs"][job]["steps"] if s.get("uses", "").startswith(action + "@")]
    upload, download = uses("installer", "actions/upload-artifact")[0], uses("release", "actions/download-artifact")[0]
    assert upload["name"] == download["name"] == "Alfred-Setup"
    assert upload["path"] == "windows-mvp/dist/Alfred-Setup-*.exe"  # one file, so it's at the artifact's top
    script = "\n".join(s.get("run", "") for s in release["steps"])
    assert "windows-mvp/jarvis/__init__.py" in script and 'setup="Alfred-Setup-$version.exe"' in script
    assert "gh release create" in script and "--target \"$GITHUB_SHA\"" in script and "--clobber" in script
    init = (MVP / "jarvis" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r'^__version__ = "([^"]*)"', init, re.M)[1] == __version__
    notes = (PACKAGING / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    for placeholder in ("{{VERSION}}", "{{SHA256}}", "{{COMMIT}}"):
        assert placeholder in notes and f"'{placeholder}'" in script
    assert "Run anyway" in notes and ".Hash.ToLower()" in notes  # Get-FileHash alone shows upper case
    demo = next(s for s in release["steps"] if s.get("id") == "prep")["env"]["DEMO_URL"]
    assert demo == "https://dhruvavalluru.github.io/Jarvis-Hacks/" and 'grep -v -F "$DEMO_URL"' in script
    assert len([line for line in notes.splitlines() if demo in line]) == 1  # what's left out when it doesn't load


# The workflows' bash steps, run here as GitHub runs them on ubuntu-latest.
bash_steps = pytest.mark.skipif(sys.platform == "win32" or not (shutil.which("bash") and shutil.which("sha256sum")),
                                reason="runs the workflows' bash steps (Linux)")


def _run_step(run: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    script = cwd.parent / f"{cwd.name}-step.sh"
    script.write_text(run, encoding="utf-8")
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=cwd,
                          env={**os.environ, **env}, capture_output=True, text=True, timeout=60)


@bash_steps
def test_the_release_notes_come_out_right(tmp_path):
    """The release job's notes step, on a stand-in installer: everything is filled in, the checksum is the
    lower-case one the notes tell people to compare, and the web demo line is only there when the demo loads
    (curl is a stand-in too)."""
    yaml = pytest.importorskip("yaml")
    desktop = yaml.safe_load((WORKFLOWS / "alfred-desktop.yml").read_text(encoding="utf-8"))
    prep = next(s for s in desktop["jobs"]["release"]["steps"] if s.get("id") == "prep")
    demo = prep["env"]["DEMO_URL"]
    repo = tmp_path / "repo"
    for path in ("jarvis/__init__.py", "packaging/RELEASE_NOTES.md"):
        (repo / "windows-mvp" / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(MVP / path, repo / "windows-mvp" / path)
    setup = f"Alfred-Setup-{__version__}.exe"
    (repo / "release").mkdir()
    (repo / "release" / setup).write_bytes(b"MZ stand-in")
    sha256 = hashlib.sha256(b"MZ stand-in").hexdigest()
    for loads in (True, False):
        temp = tmp_path / f"demo-loads-{loads}"
        (temp / "bin").mkdir(parents=True)
        (temp / "bin" / "curl").write_text(f"#!/bin/sh\nexit {0 if loads else 22}\n", encoding="utf-8")
        (temp / "bin" / "curl").chmod(0o755)
        out = _run_step(prep["run"], repo, {
            **prep["env"], "EXTRA_NOTES": "Built for HackNC.", "GITHUB_SHA": "0123abc",
            "PATH": f"{temp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(temp), "GITHUB_OUTPUT": str(temp / "output.txt")})
        assert out.returncode == 0, out.stdout + out.stderr
        notes = (temp / "notes.md").read_text(encoding="utf-8")
        assert "{{" not in notes and f"`{sha256}`" in notes and setup in notes and "0123abc" in notes
        assert (repo / "release" / "SHA256SUMS.txt").read_text(encoding="utf-8") == f"{sha256}  {setup}\n"
        assert notes.rstrip().endswith("## About this build\n\nBuilt for HackNC.")
        assert (demo in notes) is loads and ("::warning::" in out.stdout) is not loads
        assert (temp / "output.txt").read_text(encoding="utf-8") == f"version={__version__}\nsetup={setup}\n"


@bash_steps
def test_pages_waits_for_the_demo(tmp_path):
    """pages.yml can reach main before demo/ does: without demo/build.py it warns, publishes nothing and stays
    green. With it, a build that makes no index.html fails, and one that does goes on to publish."""
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
    build = next(s for s in steps if s.get("id") == "build")
    later = steps[steps.index(build) + 1:]
    assert later and all(s["if"] == "steps.build.outputs.built == 'true'" for s in later)
    makes_site = ("from pathlib import Path\n"
                  "site = Path(__file__).parent / 'dist' / 'site'\n"
                  "site.mkdir(parents=True)\n"
                  "(site / 'index.html').write_text('<title>Alfred</title>')\n")
    for name, build_py, ok, built in (("no-demo", None, True, "false"), ("no-site", "pass\n", False, None),
                                      ("demo", makes_site, True, "true")):
        repo = tmp_path / name
        (repo / "demo").mkdir(parents=True)
        if build_py:
            (repo / "demo" / "build.py").write_text(build_py, encoding="utf-8")
        output, summary = tmp_path / f"{name}-output.txt", tmp_path / f"{name}-summary.md"
        out = _run_step(build["run"], repo, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)})
        assert (out.returncode == 0) is ok, out.stdout + out.stderr
        written = output.read_text(encoding="utf-8") if output.exists() else ""
        assert written == (f"built={built}\n" if built else "")
    assert "Nothing was deployed" in (tmp_path / "no-demo-summary.md").read_text(encoding="utf-8")


def _pascal_routine(source: str, header: str) -> str:
    start = source.index(header)
    return source[start:source.index("\nend;", start)]


def test_an_update_closes_alfred_unless_it_surely_isnt_running():
    """Only a clean "not running" (0) from the PowerShell check skips closing Alfred. If PowerShell can't run
    (AppLocker or WDAC on a managed PC: -1), Alfred may be running with its files locked, so Setup closes it
    anyway; StopAlfred then falls back to taskkill."""
    iss = (PACKAGING / "alfred.iss").read_text(encoding="utf-8").replace("\r\n", "\n")
    prepare = _pascal_routine(iss, "function PrepareToInstall(")
    assert "AlfredProcesses(ExpandConstant('{app}'), 'check')" in prepare
    checks = re.findall(r"if (.+) then\n\s+Exit;", prepare)
    assert checks == ["not FileExists(ExpandConstant('{app}\\{#AppExe}'))", "Running = 0"]
    assert prepare.rstrip().endswith("StopAlfred(ExpandConstant('{app}'));")
    stop = _pascal_routine(iss, "procedure StopAlfred(")
    assert "if AlfredProcesses(Dir, 'stop') <> 0 then" in stop
    assert "taskkill.exe'), '/F /T /IM {#AppExe}'" in stop and "taskkill.exe'), '/F /T /IM {#CliExe}'" in stop
    assert "Result := -1;" in _pascal_routine(iss, "function AlfredProcesses(")  # PowerShell didn't start


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


PARSE_PS1 = """$bad = 0
foreach ($file in $files) {
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$null, [ref]$errors)
  foreach ($e in $errors) { $bad++; Write-Output ("{0}:{1}: {2}" -f $file, $e.Extent.StartLineNumber, $e.Message) }
}
exit $bad
"""


@pytest.mark.skipif(not (shutil.which("powershell") or shutil.which("pwsh")),
                    reason="needs PowerShell (the Windows CI run has it)")
def test_the_powershell_scripts_parse():
    """A script PowerShell can't parse doesn't run at all, e.g. "build\\$art: ..." (a ':' after a variable).
    Sent as -EncodedCommand, so no execution policy gets in the way."""
    scripts = sorted(str(path) for path in PACKAGING.glob("*.ps1"))
    assert len(scripts) >= 5
    quoted = ", ".join("'" + script.replace("'", "''") + "'" for script in scripts)
    command = base64.b64encode(f"$files = @({quoted})\n{PARSE_PS1}".encode("utf-16-le")).decode()
    for shell in filter(None, (shutil.which("powershell"), shutil.which("pwsh"))):
        out = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, f"{shell}: {out.stdout}{out.stderr}"


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
