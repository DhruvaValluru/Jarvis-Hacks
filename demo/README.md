# Alfred's web demo

A "try it out" page for people without a Windows PC (hackathon judges, for one). It is **Alfred's real
window and real setup** (the files in `windows-mvp/jarvis/app/ui/`), running in any browser with no
server, next to a **simulated PC**: a small pretend Windows desktop, plus a laptop paired with it. You
type or click a command, and you see what Alfred does: the same status lines, approvals, replies and
activity log as the Windows app, while the simulated PC shows the work.

It never touches the viewer's computer. A banner says so on every page, and links to the
[Windows download](https://github.com/DhruvaValluru/Jarvis-Hacks/releases/latest) and the
[source](https://github.com/DhruvaValluru/Jarvis-Hacks). There is no AI behind it: each command is matched
to one of a few scripted examples. Anything else gets an honest answer and three examples to try.

## Build and preview

```
python3 demo/build.py            # writes demo/dist/site and demo/dist/artifact (git-ignored)
cd demo/dist/site
python3 -m http.server 8000      # then open http://localhost:8000/
```

Python 3 only, no packages. `--out DIR` writes somewhere else. The build stops with a message if the
app's UI changed in a way the demo doesn't expect (see below), instead of making a broken demo.

- **`dist/site/`** is a static site for GitHub Pages, served from a subpath
  (`https://dhruvavalluru.github.io/Jarvis-Hacks/`), so every URL in it is relative. It has a `.nojekyll`.
- **`dist/artifact/`** is the same demo for a claude.ai artifact. Its `index.html` has no `<!doctype>`,
  `<html>`, `<head>` or `<body>` (the host wraps it) and starts with `<title>Alfred Live Demo</title>`.
  Publish the other files next to it at the same paths. It makes no requests to other sites.

Light and dark follow the system. A host can choose with `data-theme="light"` or `"dark"` on `<html>`;
the app's own CSS honours that too, and without the attribute it looks exactly as before.

## Try

The banner's buttons send these, one click each:

| Example | What Alfred does (real tool names) |
|---|---|
| Open Notepad and type a note | `open_app`, `ui_set_text`: the note is typed into Notepad |
| Play some music at 30% | `media_control`, `set_volume`: the player plays, the volume flyout shows |
| How is my PC doing? | `system_status`, `open_app` (Task Manager), `ui_read_text` |
| What's on my screen? | `list_windows`, `screenshot`: the reply describes the simulated desktop |
| Email Sam the quarterly report | finds the file, writes the email in Outlook (`ui_click`, `ui_set_text`), then asks: **Allow** sends it, **Deny** doesn't, no answer in 60 s means no |
| Copy the report to my laptop | `copy_file` asks here, then Alfred on the laptop asks its own user (you may answer on the laptop, or its "user" allows it) |
| Fill in a form | computer use: `screenshot`, `left_click` and `type` with a visible pointer, then asks before clicking Submit |
| Remind me in 10 seconds | `set_timer`, then the reminder arrives (long timers come after 8 s, and the log says so) |

Simple commands take the fast path, as in `jarvis/router.py` ("open notepad", "volume 30", "next song",
"what time is it", "what's open", "timer 5 seconds"). **STOP** (or Esc then Enter, or Ctrl+Alt+Backspace)
stops a run at once, like the real hard stop, and Alfred is ready again a moment later. Commands sent
while one runs wait their turn. **Replay the setup** opens the real 10-step setup on a new simulated PC:
any key in the right shape is accepted (nothing you paste leaves the tab), the speech model download
and the Telegram link are simulated, and finishing it returns to the main window. Voice mode and
Telegram say they need the Windows app.

## How it maps to the real app

| Real app | Web demo |
|---|---|
| `jarvis/app/server.py`, `app/voice.py`, `onboarding/routes.py`: the local API at `127.0.0.1` | `src/demo-api.js` wraps `fetch` before `app.js` runs and answers every `/api/*` call with the same routes, shapes and status codes. `/api/events` is a streamed `text/event-stream` body with the same event types (`command`, `status`, `reply`, `done`, `approval`, `approval_closed`, `announce`, `stop`, `state`, `ready`, ...). |
| `jarvis/service/core.py` and the engine process | one command at a time, a queue of 20, approvals with a one-time token and a 60 s timeout, a hard Stop that ends the run and restarts, a restart after settings change |
| Claude choosing tools; `tools/registry.py` writing `audit.jsonl` | `src/scenarios.js`: scripted runs that print the brain's status lines (`-> open_app {'name': 'Notepad'}`) and write each call to the Activity page |
| your PC | `src/desktop.js` and `src/demo.css`: the simulated desktop and laptop |
| settings, Credential Manager, `onboarding.json` | kept in the page; the setup's progress, settings and key hints (never keys) in `sessionStorage`, so the setup and the main window agree |
| the sign-in key in `<meta name="alfred-key">` | the build puts in a dummy one; the stand-in ignores it |

What the build changes in its copies of the UI, each checked to match exactly once (or the build stops):
absolute paths become relative (`/onboarding/` becomes `onboarding/index.html`, `/` becomes
`../index.html`, `/theme/aero.css` becomes `../theme/aero.css`, ...), the address-bar clean-up after
sign-in goes (there is no sign-in link to hide), links to other sites in the setup become plain text
that names the site, and the demo's banner, simulated PC and scripts are added. The data notice comes
from `windows-mvp/PRIVACY.md`.

## Checks

- `windows-mvp/tests/test_demo_build.py` (run by `test.bat` and CI) builds into a temporary folder and
  checks both copies: every rewrite applied, the artifact page has no document tags and starts with its
  title, no links to other sites but the two GitHub ones, and the CSS theme pattern.
- `demo/tests/e2e.mjs` is a local tool that drives the built demo in headless Chromium, served under a
  subpath: every example, Allow, Deny and an expired approval, STOP mid-run, two commands in a row,
  Settings, Activity, the setup replayed to the end and back, and the artifact copy with a host's theme,
  at 1280x800 and 400x800 in light and dark. It fails on any console error, failed request or request to
  another site.

  ```
  node demo/tests/e2e.mjs --shots /tmp/alfred-demo-shots
  ```

  It needs Playwright for Node (`PLAYWRIGHT_MODULE`, default `/opt/node22/lib/node_modules/playwright/index.mjs`)
  and a Chromium it already has; it never installs one.
