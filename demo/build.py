#!/usr/bin/env python3
"""Build Alfred's web demo: the app's real window and setup in a browser, with no server, next to a
simulated PC that shows what Alfred does.

  python3 demo/build.py [--out DIR] [--ui DIR]

Writes two copies of the demo:
  DIR/site/      a static site for GitHub Pages. Pages serves it from a subpath
                 (https://dhruvavalluru.github.io/Jarvis-Hacks/), so every URL in it is relative.
  DIR/artifact/  the same demo for a claude.ai artifact: the main page has no <!doctype>, <html>, <head>
                 or <body> (the host wraps it) and starts with its <title>; the other files sit next to it.
DIR is demo/dist unless --out says otherwise. --ui reads the app's UI from another folder (for tests).

The UI files (windows-mvp/jarvis/app/ui/) are copied, never edited by hand, so the demo is always the
current app. The few changes a page without Alfred's server needs are made here: absolute paths become
relative, links to other sites are switched off, and the demo's own files (demo/src/) are added. Every
change must find exactly what it expects, so a change to the UI that breaks one stops the build with a
clear message instead of shipping a broken demo. Only the Python standard library is used.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
SRC = DEMO / "src"
MVP = ROOT / "windows-mvp"
UI = MVP / "jarvis" / "app" / "ui"

TITLE = "Alfred Live Demo"
# The only other sites the demo links to: the download and the source, both in the demo's banner.
REPO = "https://github.com/DhruvaValluru/Jarvis-Hacks"
ALLOWED_URLS = {REPO, f"{REPO}/releases/latest"}

UI_FILES = [
    "index.html", "app.js", "app.css", "icon.svg", "theme/aero.css",
    "onboarding/index.html", "onboarding/onboarding.js", "onboarding/onboarding.css", "onboarding/settings-card.js",
]
# demo/src files published under demo/ next to the UI. The HTML fragments are put into the pages instead.
DEMO_FILES = ["demo-api.js", "scenarios.js", "desktop.js", "demo-ui.js", "demo.css"]
FRAGMENTS = ["bar.html", "pc.html", "setup-bar.html", "settings-note.html"]

# (old, new, how many times old must appear). Absolute paths only work at the root of Alfred's own
# server; the demo is served from a subpath (Pages) or next to a host page (the artifact).
REWRITES = {
    "app.js": [
        # The real app hides its one-time sign-in link from the address bar. The demo has none, and its
        # address (with any query the host needs) must stay as it is.
        ('if (location.pathname !== "/") history.replaceState(null, "", "/");',
         "/* web demo: there is no sign-in link in the address to hide */", 1),
        ('location.replace("/onboarding/")', 'location.replace("onboarding/index.html")', 1),
    ],
    "onboarding/onboarding.js": [
        ('location.href = "/";', 'location.href = "../index.html";', 2),
    ],
    "onboarding/settings-card.js": [
        ("location.href = `/onboarding/#${step.name}`;", "location.href = `onboarding/index.html#${step.name}`;", 1),
    ],
    "onboarding/index.html": [
        ('href="/icon.svg"', 'href="../icon.svg"', 1),
        ('src="/icon.svg"', 'src="../icon.svg"', 1),
        ('href="/theme/aero.css"', 'href="../theme/aero.css"', 1),
        ('href="/onboarding/onboarding.css"', 'href="onboarding.css"', 1),
        ('src="/onboarding/onboarding.js"', 'src="onboarding.js"', 1),
        ('href="/"', 'href="../index.html"', 1),
    ],
}

MAIN_SCRIPTS = """<link rel="stylesheet" href="demo/demo.css">
<script src="demo/notice.js"></script>
<script src="demo/demo-api.js" data-page="main"></script>
<script src="demo/desktop.js" defer></script>
<script src="demo/scenarios.js" defer></script>
<script src="demo/demo-ui.js" defer></script>
"""
SETUP_SCRIPTS = """<link rel="stylesheet" href="../demo/demo.css">
<script src="../demo/notice.js"></script>
<script src="../demo/demo-api.js" data-page="setup"></script>
<script src="../demo/desktop.js" defer></script>
<script src="../demo/scenarios.js" defer></script>
<script src="../demo/demo-ui.js" defer></script>
"""

URL = re.compile(r"""https?://[^\s"'<>()\\`]+""")
LINK = re.compile(r'<a\b([^>]*?)\shref="(https?://([^/"]+)[^"]*)"([^>]*)>(.*?)</a>', re.S)
ABSOLUTE_HTML = re.compile(r"""\b(?:href|src|action)\s*=\s*["']/(?!/)""")
# A page or file named from the server's root: where scripts navigate, or a path to one of the demo's files.
ABSOLUTE_JS = re.compile(r"""(?:(?:location(?:\.href)?\s*=|location\.(?:replace|assign)\(|State\([^)]*,|\.(?:src|href)\s*=|fetch\()"""
                         r"""\s*["'`]/(?!api/|/))|["'`]/(?:onboarding|index\.html|app\.|icon\.svg|theme/|demo/)""")
ABSOLUTE_CSS = re.compile(r"""url\(\s*["']?/""")
FORBIDDEN = [
    (re.compile(r"\b(?:alert|confirm|prompt)\s*\("), "alert(), confirm() or prompt()"),
    (re.compile(r"\bwindow\.open\s*\("), "window.open()"),
    (re.compile(r"mailto:", re.I), "a mailto: link"),
    (re.compile(r"serviceWorker"), "a service worker"),
]
PAGE_TAGS = re.compile(r"<!doctype|</?(?:html|head|body)\b", re.I)
DARK_GUARDED = re.compile(
    r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(?P<body>[^{}]*)\}\s*\}')
DARK_CHOSEN = re.compile(r':root\[data-theme="dark"\] \{(?P<body>[^{}]*)\}')
DARK_BARE = re.compile(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{")
THEMED_CSS = ["theme/aero.css", "app.css", "demo/demo.css"]


class BuildError(Exception):
    pass


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")  # universal newlines: a CRLF checkout reads the same
    except OSError as e:
        raise BuildError(f"Can't read {path}: {e.strerror or e}.") from e


def replace(text: str, old: str, new: str, count: int, where: str) -> str:
    found = text.count(old)
    if found != count:
        raise BuildError(f"{where}: expected {count}x {old!r}, found {found}. The UI changed: update demo/build.py "
                         "(and the demo's stand-in if the UI now works differently).")
    return text.replace(old, new)


def switch_off_links(html: str) -> str:
    """Links to other sites become plain text that names the site: the demo makes no requests elsewhere."""
    def plain(m: re.Match) -> str:
        url, host, inner = m.group(2), m.group(3), m.group(5)
        if url in ALLOWED_URLS:
            return m.group(0)
        attrs = m.group(1) + m.group(4)
        cls = re.search(r'class="([^"]*)"', attrs)
        classes = (cls.group(1) + " " if cls else "") + "demo-nolink"
        return f'<span class="{classes}" title="The Windows app opens {host} here.">{inner} ({host})</span>'

    return LINK.sub(plain, html)  # anything left is caught by check()


def demo_data(mvp: Path) -> str:
    """demo/notice.js: the data notice (PRIVACY.md), Alfred's version and the notice's version."""
    version = re.search(r'__version__\s*=\s*"([^"]+)"', read(mvp / "jarvis" / "__init__.py"))
    terms = re.search(r'TERMS_VERSION\s*=\s*"([^"]+)"', read(mvp / "jarvis" / "consent.py"))
    if not version or not terms:
        raise BuildError("Can't find __version__ in jarvis/__init__.py or TERMS_VERSION in jarvis/consent.py.")
    data = {"version": version.group(1), "terms": terms.group(1), "notice": read(mvp / "PRIVACY.md")}
    return ('"use strict";\n// Made by demo/build.py from windows-mvp/PRIVACY.md, jarvis/__init__.py and jarvis/consent.py.\n'
            "// Don't edit: build again.\n"
            f"window.AlfredDemoData = {json.dumps(data, ensure_ascii=True, indent=1)};\n")


def fragment(name: str) -> str:
    return read(SRC / name).strip() + "\n"


def main_page(html: str) -> str:
    where = "index.html"
    html, found = re.subn(r"<head\b[^>]*>", lambda m: m.group(0) + '\n<meta name="alfred-key" content="web-demo">',
                          html, count=1)
    if not found:
        raise BuildError(f"{where}: no <head>.")
    html = replace(html, "<title>Alfred</title>", f"<title>{TITLE}</title>", 1, where)
    html = replace(html, '<script src="app.js" defer></script>', MAIN_SCRIPTS + '<script src="app.js" defer></script>', 1,
                   where)
    html = replace(html, "<body>", "<body>\n<div class=\"demo-root\">\n" + fragment("bar.html")
                   + '<div class="demo-stage">\n<div class="demo-app" id="demo-app">', 1, where)
    html = replace(html, "</body>", "</div>\n" + fragment("pc.html") + "</div>\n</div>\n</body>", 1, where)
    html = replace(html, "<h1>Settings</h1>", "<h1>Settings</h1>\n    " + fragment("settings-note.html").strip(), 1, where)
    return html


def setup_page(html: str) -> str:
    where = "onboarding/index.html"
    html = replace(html, '<script src="onboarding.js" defer></script>',
                   SETUP_SCRIPTS + '<script src="onboarding.js" defer></script>', 1, where)
    html = replace(html, '<body class="aero">', '<body class="aero">\n' + fragment("setup-bar.html"), 1, where)
    return switch_off_links(html)


def artifact_page(html: str) -> str:
    """The main page for a claude.ai artifact: the host adds the document around it, so no <!doctype>,
    <html>, <head> or <body>; it starts with the title, then the styles and scripts, then the content."""
    title = re.search(r"<title>.*?</title>\s*", html, re.S)
    if not title:
        raise BuildError("index.html: no <title>.")
    html = html[:title.start()] + html[title.end():]
    html = re.sub(r"<!doctype[^>]*>\s*", "", html, flags=re.I)
    html = re.sub(r"</?(?:html|head|body)\b[^>]*>\s*", "", html, flags=re.I)
    return title.group(0).strip() + "\n" + html.strip() + "\n"


def build_files(ui: Path) -> dict[str, bytes | str]:
    """Every published file: path -> text (or bytes for the icon)."""
    files: dict[str, bytes | str] = {}
    for name in UI_FILES:
        path = ui / name
        if not path.is_file():
            raise BuildError(f"Missing UI file: {path}")
        files[name] = path.read_bytes() if name.endswith(".svg") else read(path)
    for name, rules in REWRITES.items():
        for old, new, count in rules:
            files[name] = replace(files[name], old, new, count, name)
    files["index.html"] = main_page(files["index.html"])
    files["onboarding/index.html"] = setup_page(files["onboarding/index.html"])
    for name in DEMO_FILES:
        files[f"demo/{name}"] = read(SRC / name)
    files["demo/notice.js"] = demo_data(MVP)
    return files


def check(files: dict, artifact_main: str) -> None:
    problems = []
    for name, text in files.items():
        if isinstance(text, bytes):
            continue
        kind = name.split(" ")[0].rsplit(".", 1)[-1]
        if kind == "html" and ABSOLUTE_HTML.search(text):
            problems.append(f"{name}: an absolute path ({ABSOLUTE_HTML.search(text).group(0)}...)")
        if kind == "js":
            for m in ABSOLUTE_JS.finditer(text):
                problems.append(f"{name}: an absolute path {m.group(0)}")
        if kind == "css" and ABSOLUTE_CSS.search(text):
            problems.append(f"{name}: an absolute url(/...)")
        for m in URL.finditer(text):
            if m.group(0).rstrip(".,;") not in ALLOWED_URLS:
                problems.append(f"{name}: a link to another site: {m.group(0)}")
        for pattern, what in FORBIDDEN:
            if pattern.search(text):
                problems.append(f"{name}: {what}")
    if not artifact_main.startswith(f"<title>{TITLE}</title>"):
        problems.append(f"artifact index.html doesn't start with <title>{TITLE}</title>")
    if PAGE_TAGS.search(artifact_main):
        problems.append(f"artifact index.html still has {PAGE_TAGS.search(artifact_main).group(0)}")
    for name in THEMED_CSS:
        text = files[name]
        guarded, chosen = DARK_GUARDED.search(text), DARK_CHOSEN.search(text)
        if DARK_BARE.search(text) or not guarded or not chosen:
            problems.append(f'{name}: dark mode must be @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) '
                            '{ ... } } plus :root[data-theme="dark"] { ... }')
        elif _declarations(guarded.group("body")) != _declarations(chosen.group("body")):
            problems.append(f"{name}: the two dark mode blocks differ")
    if problems:
        raise BuildError("The built demo has problems:\n  " + "\n  ".join(problems))


def _declarations(block: str) -> list[str]:
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return [" ".join(d.split()) for d in block.split(";") if d.strip()]


def write(folder: Path, files: dict) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def build(out: Path, ui: Path = UI) -> tuple[Path, Path]:
    files = build_files(ui)
    artifact = dict(files)
    artifact["index.html"] = artifact_page(files["index.html"])
    check({**files, "index.html (artifact)": artifact["index.html"]}, artifact["index.html"])
    site_dir, artifact_dir = out / "site", out / "artifact"
    write(site_dir, {**files, ".nojekyll": ""})
    write(artifact_dir, artifact)
    return site_dir, artifact_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="build.py", description="Build Alfred's web demo (site and artifact).")
    parser.add_argument("--out", type=Path, default=DEMO / "dist", help="output folder (default: demo/dist)")
    parser.add_argument("--ui", type=Path, default=UI, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        site, artifact = build(args.out.resolve(), args.ui.resolve())
    except BuildError as e:
        print(f"build.py: error: {e}", file=sys.stderr)
        return 1
    print(f"Built the demo:\n  {site}  (GitHub Pages; preview: cd {site} && python3 -m http.server)\n  {artifact}  (claude.ai artifact)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
