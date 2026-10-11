"""The web demo's build (demo/build.py, task M4): both copies are made, every change the copies of
the app's UI need is applied, the artifact's main page is one its host can wrap, nothing points at
another site, and the app's CSS lets a page choose light or dark with data-theme. The build only
writes into a temporary folder, so this runs anywhere (Linux, and Windows in CI)."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "demo" / "build.py"
UI = ROOT / "windows-mvp" / "jarvis" / "app" / "ui"
REPO = "https://github.com/DhruvaValluru/Jarvis-Hacks"
ALLOWED = {REPO, f"{REPO}/tree/main/windows-mvp#readme"}  # the source, and how to install on Windows
URL = re.compile(r"""https?://[^\s"'<>()\\`]+""")

pytestmark = pytest.mark.skipif(not BUILD.is_file(), reason="demo/ isn't in this checkout")


def build(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILD), "--out", str(out), *extra], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("demo")
    result = build(out)
    assert result.returncode == 0, result.stderr
    return out


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def pages(folder: Path):
    return [p for p in folder.rglob("*") if p.suffix in (".html", ".js", ".css")]


def test_both_copies_are_built(built):
    for copy in ("site", "artifact"):
        for name in ("index.html", "app.js", "app.css", "logo.png", "theme/aero.css", "onboarding/index.html",
                     "onboarding/onboarding.js", "onboarding/onboarding.css", "onboarding/settings-card.js",
                     "demo/demo-api.js", "demo/scenarios.js", "demo/desktop.js", "demo/demo-ui.js", "demo/demo.css",
                     "demo/notice.js"):
            assert (built / copy / name).is_file(), f"{copy}/{name}"
    assert (built / "site" / ".nojekyll").is_file()  # GitHub Pages serves the files as they are


def test_the_demo_is_the_current_ui(built):
    # Apart from the rewrites, the copies are the app's own files.
    assert text(built / "site" / "app.css") == text(UI / "app.css")
    assert text(built / "site" / "theme" / "aero.css") == text(UI / "theme" / "aero.css")
    app = text(built / "site" / "app.js")
    assert "function handle(event)" in app and app.count("\n") == text(UI / "app.js").count("\n")
    notice = text(built / "site" / "demo" / "notice.js")
    assert "Where your data goes" in notice  # the real data notice (PRIVACY.md)


def test_the_stand_in_runs_before_the_app(built):
    main = text(built / "site" / "index.html")
    assert main.index('src="demo/demo-api.js"') < main.index('src="app.js"')
    assert '<meta name="alfred-key"' in main
    assert "<title>Alfred Live Demo</title>" in main
    setup = text(built / "site" / "onboarding" / "index.html")
    assert setup.index('src="../demo/demo-api.js" data-page="setup"') < setup.index('src="onboarding.js"')


def test_paths_are_relative(built):
    for copy in ("site", "artifact"):
        folder = built / copy
        app = text(folder / "app.js")
        assert 'replaceState(null, "", "/")' not in app
        assert 'location.replace("onboarding/index.html")' in app and 'location.replace("/onboarding/")' not in app
        onboarding = text(folder / "onboarding" / "onboarding.js")
        assert onboarding.count('location.href = "../index.html";') == 2 and 'location.href = "/"' not in onboarding
        assert "location.href = `onboarding/index.html#${step.name}`" in text(folder / "onboarding" / "settings-card.js")
        for page in folder.rglob("*.html"):
            assert not re.search(r"""\b(?:href|src)\s*=\s*["']/(?!/)""", text(page)), page


def test_artifact_page_is_for_a_host_to_wrap(built):
    main = text(built / "artifact" / "index.html")
    assert main.startswith("<title>Alfred Live Demo</title>")
    assert not re.search(r"<!doctype|</?(?:html|head|body)\b", main, re.I)
    assert '<div class="demo-root">' in main and 'id="demo-pc"' in main
    # Its other pages keep their whole document.
    assert text(built / "artifact" / "onboarding" / "index.html").lstrip().lower().startswith("<!doctype html>")


def test_nothing_points_at_another_site(built):
    found = set()
    for folder in (built / "site", built / "artifact"):
        for page in pages(folder):
            body = text(page)
            for url in URL.findall(body):
                assert url in ALLOWED, f"{page.relative_to(built)}: {url}"
                found.add(url)
            for forbidden in ("window.open(", "mailto:", "serviceWorker"):
                assert forbidden not in body, f"{page.relative_to(built)}: {forbidden}"
            assert not re.search(r"\b(?:alert|confirm|prompt)\s*\(", body), page
    assert found == ALLOWED  # the banner links how to install and the source
    setup = text(built / "site" / "onboarding" / "index.html")
    assert "console.anthropic.com" in setup and 'class="aero-link demo-nolink"' in setup  # named, not linked


def _dark_blocks(css: str) -> tuple[list, list]:
    guarded = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([^{}]*)\}\s*\}', css)
    chosen = re.search(r':root\[data-theme="dark"\] \{([^{}]*)\}', css)
    assert guarded and chosen

    def declarations(block):
        return [" ".join(d.split()) for d in re.sub(r"/\*.*?\*/", "", block, flags=re.S).split(";") if d.strip()]

    return declarations(guarded.group(1)), declarations(chosen.group(1))


@pytest.mark.parametrize("name", ["theme/aero.css", "app.css"])
def test_app_css_lets_a_page_choose_light_or_dark(name):
    css = text(UI / name)
    assert not re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{", css)  # no unguarded dark block
    guarded, chosen = _dark_blocks(css)
    assert guarded and guarded == chosen  # data-theme="dark" gets exactly the dark mode values


def test_demo_css_follows_the_same_pattern():
    guarded, chosen = _dark_blocks(text(ROOT / "demo" / "src" / "demo.css"))
    assert guarded and guarded == chosen


def test_pages_set_their_own_background():
    assert re.search(r"body \{[^}]*background: var\(--aero-sky-mid\)", text(UI / "app.css"))
    assert re.search(r"body \{[^}]*background: var\(--aero-sky-mid\)", text(UI / "onboarding" / "onboarding.css"))


def test_a_ui_change_stops_the_build(tmp_path):
    ui = tmp_path / "ui"
    shutil.copytree(UI, ui)
    app = ui / "app.js"
    app.write_text(text(app).replace('history.replaceState(null, "", "/")', 'history.replaceState(null, "", "/home")'),
                   encoding="utf-8")
    result = build(tmp_path / "out", "--ui", str(ui))
    assert result.returncode == 1
    assert "app.js" in result.stderr and "The UI changed" in result.stderr
    assert not (tmp_path / "out" / "site").exists()  # nothing half built is left to publish
