# Computer use: plan for the Windows MVP

Goal: when UI Automation and COM can't reach an app (games, canvas/custom-drawn UIs, some web pages, remote desktops), Jarvis falls back to **Claude computer use**. Claude looks at screenshots and drives the real mouse and keyboard.

This matches the README's priority order: plugin API → COM → cloud API → UIA → **vision (computer use) as a last resort**. Computer use is the fallback. It doesn't replace UIA.

## Where we are today

| Piece | State | File |
|---|---|---|
| Claude tool loop | Works, custom tools only | `windows-mvp/jarvis/brain.py` |
| `screenshot` tool | Custom tool. Full-screen grab, `thumbnail(1600)`, no coordinate mapping | `windows-mvp/jarvis/tools/system.py` |
| Mouse/keyboard by coordinates | **Missing.** `send_keys` and the UIA `Click()` fallback only target named controls | `windows-mvp/jarvis/tools/uia.py` |
| DPI awareness | **Missing.** Screenshots and cursor coordinates disagree on scaled displays (125%/150%) | none |
| Model | `claude-opus-5-5` with server-side fallbacks on | `windows-mvp/jarvis/config.py` |
| SDK | `anthropic>=1.13`. 1.13 already parses the computer toolset blocks (`toolset_name`, `ComputerToolUseBlock`) | `windows-mvp/requirements.txt` |

Baseline: `python -m pytest tests/test_core.py` passes (4/4) on this branch.

## API facts that set the design

1. **On `claude-opus-5-5`, the only computer-use form is the toolset.** Use `{"type": "computer_toolset_20260801"}` with no beta header, no `name` and no display size. The old `computer_20251124` / `computer-use-2025-11-24` form **returns a 400** on this model through the Claude API. Most online examples still use the old form, so don't copy them.
2. **Each action is its own `tool_use` block.** The block's `name` is the action (`screenshot`, `left_click`, `type`, `key`, `scroll`, `zoom`, ...) and it carries `"toolset_name": "computer"`. The action is **not** in `input.action`.
3. **There are 17 members:** `screenshot`, `zoom` (`region [x0,y0,x1,y1]`), `left_click` / `right_click` / `middle_click` / `double_click` / `triple_click` (`coordinate?`, `text?` modifiers), `left_click_drag` (`start_coordinate`, `coordinate`), `mouse_move`, `left_mouse_down`, `left_mouse_up`, `cursor_position`, `scroll` (`scroll_direction`, `scroll_amount`, `coordinate?`, `text?`), `type` (`text`), `key` (`text` like `"Return"`, `"ctrl+s"`, `repeat?`), `hold_key` (`text`, `duration`), `wait` (`duration`). Any of them can be turned off with `configs`, for example `{"zoom": {"enabled": false}}`.
4. **Claude can send several actions in one turn.** Run them in order and stop at the first failure. Every later block in that turn gets `is_error: true` with the exact text `Not executed: an earlier computer action in this turn failed.`
5. **Every `tool_result` must echo `"toolset_name": "computer"`.** The API rejects a result without it. Only `screenshot` and `zoom` return an image. The others can return `"OK"`.
6. **The API doesn't resize screenshots, so we have to.** The limit is 2576 px on the long edge and about 3.75 MP. Once a request holds more than 20 images, each side must be 2000 px or less. Aim for **about 1280×720**. Claude's coordinates are in **our screenshot's pixel space**, so we map them back to real screen pixels before clicking.
7. **Never remove old screenshots from history.** On Opus 5.5, editing earlier turns invalidates the thinking blocks that come after them ("preserved thinking"). Keep screenshots small instead. Our `reset()` already starts a fresh conversation, and that's fine.
8. **A name clash breaks the current code.** Toolset blocks are still `type: "tool_use"`, so today's loop would send `screenshot` to our custom `screenshot` tool, which returns an unscaled image and no `toolset_name`, and the request fails. Every other action would come back "Unknown tool". The custom `screenshot` tool must be renamed or removed while the toolset is on.

## Outline of the work

### Step 1: Windows input and capture layer (new `jarvis/tools/computer.py`)
- [ ] At startup, call `SetProcessDpiAwareness(2)` (per-monitor v2) before any window or screenshot code runs, so capture and `SendInput` use the same physical pixels.
- [ ] Add a `Screen` helper that grabs the **primary monitor** (MVP; multi-monitor later). It resizes to a target long edge of about 1280 px, keeps the `scale` factor, and returns base64 PNG.
- [ ] Add `to_screen(x, y)`, which divides by `scale`, adds the monitor offset and clamps to bounds.
- [ ] Inject input with `SendInput` through ctypes (or `pywin32`). Cover move, left/right/middle down/up, double/triple click, drag, wheel (vertical and horizontal), and Unicode typing (`KEYEVENTF_UNICODE`) so typing doesn't depend on keyboard layout.
- [ ] Map xdotool-style key names (`Return`, `ctrl+s`, `alt+Tab`, `super`, `Page_Down`, `F5`, ...) to virtual-key codes, and support `repeat`, `hold_key` and modifier `text` on clicks and scrolls.
- [ ] Implement `zoom`: crop `region` from a fresh full-resolution grab (region is in screenshot space, so map it), then resize to fit the limits.
- [ ] Implement `cursor_position`: `GetCursorPos` mapped back into screenshot space.

### Step 2: wire the toolset into the brain (`jarvis/brain.py`)
- [ ] Add `{"type": "computer_toolset_20260801"}` to `tools` behind a setting (`JARVIS_COMPUTER_USE=1`, default off at first). Keep `cache_control` on the entry.
- [ ] Rename the custom `screenshot` tool (for example `look_at_screen`) or leave it out of the schemas while the toolset is on.
- [ ] In `_loop`, send blocks with `getattr(call, "toolset_name", None) == "computer"` to `computer.run(call.name, call.input)`. Run them **in order**, stop at the first failure and send the halt text for the rest. Add `toolset_name` to every one of those results.
- [ ] Raise `max_steps` when computer use is in play, since GUI tasks often take 20–50 steps. Make `max_steps` a separate setting, for example `JARVIS_CU_MAX_STEPS=40`.
- [ ] Update the system prompt to say: use computer use only after UIA, COM and dedicated tools fail or can't see the app; take a screenshot first; confirm the result with a screenshot after acting.
- [ ] Check that refusal fallbacks still work with the toolset. Every model the toolset supports is a valid fallback target, so this should be fine, but test it once.

### Step 3: safety, so pixel clicks get the same guardrails as UIA
- [ ] Before each click, look up the element under the target point with UIA `ControlFromPoint` and run the existing `RISKY_WORDS` check on its name. "Send", "Delete", "Buy" and similar words ask for confirmation, as `ui_click` does now.
- [ ] Before `type`, check whether the focused element is a password field (UIA `IsPassword`). If it is, refuse and say so.
- [ ] Log every action with mapped coordinates and the resolved element name to `audit.jsonl` (reuse `registry.audit`).
- [ ] Add a kill switch: a global hotkey (for example Ctrl+Alt+J) or moving the mouse to a screen corner aborts the loop. Ctrl+C in the console still works.
- [ ] Stop on the lock screen, UAC or secure desktop. Screenshots there come back black and input is blocked, so tell the user instead of clicking blind.

### Step 4: tests
- [ ] Add an offline unit test in `tests/test_core.py`, written like `test_brain_loop`. A fake client returns one turn with `screenshot` + `left_click` + `type` toolset blocks. Assert that results come back in order, each carries `toolset_name`, and only the screenshot result has an image. Add a second case where action 2 fails and action 3 gets the halt text.
- [ ] Add unit tests for coordinate scaling round-trips and the key-name → VK mapping. Both are pure functions and run on Linux.
- [ ] Extend the Windows smoke test in `tests/smoke_windows.py`, which runs on `windows-latest` in CI. Open Notepad, take a screenshot, click the centre of its edit area by coordinate, type through the computer layer, then read the text back through UIA (`ui_read_text`) to confirm. No API key is needed because this drives `computer.run` directly.
- [ ] Do a manual end-to-end run with a real key: `jarvis.bat --do "open Paint and draw a square"`. Paint's canvas exposes nothing to UIA, so this is the target case.

### Step 5: docs and settings
- [ ] Add `JARVIS_COMPUTER_USE` and `JARVIS_CU_MAX_STEPS` to `.env.example` and `config.py`.
- [ ] Update the `windows-mvp/README.md` "Things to try" table and the Limits list: it works on the primary monitor only, and not on elevated apps unless Jarvis is elevated.

## Known limits (same as UIA, by design)
- Elevated (admin) windows reject input from a non-elevated Jarvis (UIPI).
- The lock screen, UAC and Ctrl+Alt+Del are off-limits.
- DRM video shows up black in screenshots.
- Computer use is slower and costs more than UIA (one screenshot per step), so the router and prompt should keep it as a fallback.

## Suggested first PR (smallest piece that works end to end)
Steps 1 and 2 with zoom turned off (`configs: {"zoom": {"enabled": false}}`), plus the offline unit test from step 4. Then add zoom, the safety hooks and the CI smoke test in a second PR.
