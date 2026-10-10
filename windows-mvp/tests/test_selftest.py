"""The self-test (jarvis/selftest.py) that alfred-cli.exe --selftest and test.bat run: its checks, its
report, and the engine round trip with the real engine. Windows' own modules and files are checked by CI
on the built and installed app."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, selftest  # noqa: E402
from jarvis.selftest import Check  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, value in {"LOCALAPPDATA": str(tmp_path), "JARVIS_IGNORE_USER_SETTINGS": "1",
                       "ANTHROPIC_API_KEY": "sk-not-for-the-self-test", "JARVIS_TOKEN": "pairing-secret"}.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def test_check_lines_and_summary():
    assert Check("a", True, "1.0 s").line() == "[ok] a: 1.0 s"
    assert Check("b", False, "missing").line() == "[XX] b: missing"
    assert Check("c", None).line() == "[--] c"
    ok, text = selftest.summary([Check("a", True), Check("c", None)])
    assert ok and text == "RESULT: PASSED (1 checks, 1 not needed here)"
    ok, text = selftest.summary([Check("a", True), Check("b", False), Check("d", False)])
    assert not ok and text.startswith("RESULT: FAILED: 2 of 3 checks") and "b, d" in text


def test_import_failures_are_reported_and_the_rest_still_run():
    def importer(name):
        if name == "missing":
            raise ImportError("No module named 'missing'")
        if name == "exits":
            raise SystemExit(3)  # a module that calls sys.exit() on import must not end the self-test

    checks = list(selftest.import_checks(["fine", "missing", "exits", "also_fine"], importer))
    assert [(c.name, c.ok) for c in checks] == [
        ("import fine", True), ("import missing", False), ("import exits", False), ("import also_fine", True)]
    assert "No module named 'missing'" in checks[1].detail and "SystemExit" in checks[2].detail


def test_file_checks(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "silero_vad_v6.onnx").write_bytes(b"x")
    (tmp_path / "bin").mkdir()

    def locate(package):
        if package == "gone":
            raise ModuleNotFoundError("No module named 'gone'")
        return tmp_path

    checks = list(selftest.file_checks(
        [("pkg", "assets/*.onnx"), ("pkg", "bin/missing.dll"), ("pkg", "bin"), ("gone", "x.txt")], locate))
    assert [c.ok for c in checks] == [True, False, False, False]  # a folder isn't a file
    assert checks[0].detail.endswith("silero_vad_v6.onnx")
    assert "missing in" in checks[1].detail and "can't find gone" in checks[3].detail


def test_the_source_tree_has_what_the_bundle_needs():
    checks = list(selftest.bundle_checks())
    assert checks and all(c.ok for c in checks), [c.line() for c in checks if not c.ok]
    names = " ".join(c.name for c in checks)
    assert "ui/index.html" in names and "PRIVACY.md" in names and "requirements.lock.txt" in names


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows these are real checks (CI runs them)")
def test_windows_checks_are_skipped_elsewhere():
    skipped = Check("Windows modules and files", None, "not checked: this isn't Windows")
    assert list(selftest.windows_checks()) == [skipped]


def _fake_node(stdout="v22.23.3\n", code=0, error=None, calls=None):
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        if error:
            raise error
        if "--version" in cmd:
            return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A Telegram bridge folder, as bridge_command() would point at it."""
    folder = tmp_path / "telegram"
    folder.mkdir()
    (folder / "index.mjs").write_text("// bridge")
    node = tmp_path / "node" / "node.exe"
    monkeypatch.setattr("jarvis.service.bridge.bridge_command", lambda: [str(node), str(folder / "index.mjs")])
    return folder


def test_node_from_source(bridge, monkeypatch):
    monkeypatch.setattr(config, "frozen", lambda: False)
    checks = list(selftest.node_checks(_fake_node(error=FileNotFoundError("node"))))
    assert [c.ok for c in checks] == [True, None]  # no Node on PATH: only Telegram needs it
    checks = list(selftest.node_checks(_fake_node()))
    assert [(c.name, c.ok) for c in checks] == [("Telegram bridge (telegram/index.mjs)", True), ("Node.js", True),
                                                ("Telegram bridge packages", None)]
    (bridge / "node_modules").mkdir()
    calls = []
    checks = list(selftest.node_checks(_fake_node(calls=calls)))
    assert checks[-1] == Check("Telegram bridge packages load", True, "spectrum-ts")
    cmd, kwargs = calls[-1]
    assert cmd[1:4] == ["--input-type=module", "-e", "await import(process.argv[1])"]
    assert cmd[4] == (bridge / "spectrum.mjs").as_uri() and kwargs["cwd"] == str(bridge) and kwargs["timeout"]
    checks = list(selftest.node_checks(_fake_node(stdout="v20.1.0\n")))
    assert checks[1].ok is False and "spec: Node 22" in checks[1].detail


def test_node_in_the_installed_app_is_required(bridge, monkeypatch):
    monkeypatch.setattr(config, "frozen", lambda: True)
    checks = list(selftest.node_checks(_fake_node(error=FileNotFoundError("node.exe"))))
    assert checks[1].name == "Node.js (node\\node.exe)" and checks[1].ok is False
    checks = list(selftest.node_checks(_fake_node()))
    assert checks[-1] == Check("Telegram bridge packages", False, f"missing: {bridge / 'node_modules'}")
    checks = list(selftest.node_checks(_fake_node(code=1, stdout="")))
    assert checks[1].ok is False


def test_isolated_env_hides_this_pcs_settings_and_restores_them(env, tmp_path):
    os.environ.pop("JARVIS_SPEAK", None)
    with selftest.isolated_env(tmp_path / "selftest") as folder:
        assert os.environ["LOCALAPPDATA"] == str(folder)
        assert os.environ["ANTHROPIC_API_KEY"] == "" and os.environ["JARVIS_TOKEN"] == ""
        assert os.environ["JARVIS_DISCOVER"] == "0" and os.environ["JARVIS_SERVE"] == "0"
        assert config.load().has_llm is False
    assert os.environ["LOCALAPPDATA"] == str(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-not-for-the-self-test"
    assert os.environ["JARVIS_TOKEN"] == "pairing-secret"
    assert "JARVIS_SPEAK" not in os.environ


def test_engine_round_trip_with_the_real_engine(env):
    checks = {c.name: c for c in selftest.engine_checks(timeout=60)}
    for name in ("Engine starts", 'Command "reset"', "Stop, then a fresh engine", 'Command "reset" after Stop',
                 "Engine shuts down"):
        assert checks[name].ok, checks[name].line()
    fast = checks[f'Command "{selftest.FAST_COMMAND}"']
    if importlib.util.find_spec("psutil"):
        assert fast.ok and fast.detail.startswith("It's "), fast.line()
    else:  # system_status needs psutil (it's in the lock file, so on Windows it's always there)
        assert fast.ok is False and "psutil" in fast.detail
    assert os.environ["LOCALAPPDATA"] == str(env) and os.environ["ANTHROPIC_API_KEY"] == "sk-not-for-the-self-test"
    assert not (env / "Jarvis").exists()  # the engine used its own throwaway folder


def test_an_engine_that_never_starts_is_reported(env, tmp_path, monkeypatch):
    from jarvis.service import AlfredService

    monkeypatch.setattr(AlfredService, "RESTART_DELAYS", (0.05, 0.05, 0.05))  # give up quickly
    script = tmp_path / "broken_engine.py"
    script.write_text("import sys\nprint('engine failed to load its tools', file=sys.stderr)\nsys.exit(2)\n")
    checks = list(selftest.engine_checks(timeout=15, engine_cmd=[sys.executable, str(script)]))
    assert checks[0].name == "Engine starts" and checks[0].ok is False
    assert "engine failed to load its tools" in checks[0].detail
    assert checks[-1] == Check("Engine shuts down", True, "state off")


def test_run_keeps_going_after_a_broken_section_and_main_exit_codes(monkeypatch, capsys):
    def broken():
        yield Check("first", True)
        raise RuntimeError("bug")

    lines = []
    checks = selftest.run([("A", broken), ("B", lambda: iter([Check("second", True)]))], out=lines.append)
    assert [c.name for c in checks] == ["first", "A (self-test error)", "second"]
    assert lines == ["--- A", "[ok] first", "[XX] A (self-test error): RuntimeError: bug", "--- B", "[ok] second"]

    monkeypatch.setattr(selftest, "SECTIONS", (("Fine", lambda: iter([Check("fine", True)])),))
    assert selftest.main([]) == 0
    assert "RESULT: PASSED" in capsys.readouterr().out
    monkeypatch.setattr(selftest, "SECTIONS", (("Bad", lambda: iter([Check("bad", False, "why")])),))
    assert selftest.main([]) == 1
    out = capsys.readouterr().out
    assert "[XX] bad: why" in out and "RESULT: FAILED" in out


def test_python_dash_m_runs_it():
    out = subprocess.run([sys.executable, "-m", "jarvis.selftest", "--help"], capture_output=True, text=True,
                         cwd=str(config.MVP_DIR), timeout=60)
    assert out.returncode == 0 and "Check that this copy of Alfred is complete" in out.stdout


def test_exit_now_keeps_the_report_and_the_exit_code():
    code = "from jarvis import selftest; print('RESULT: FAILED: 1 of 2 checks', end=''); selftest.exit_now(1)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(config.MVP_DIR),
                         timeout=60)
    assert out.returncode == 1 and out.stdout == "RESULT: FAILED: 1 of 2 checks"
