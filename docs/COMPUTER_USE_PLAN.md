# Computer use: plan for the Windows MVP

Goal: when UI Automation and COM can't reach an app (games, canvas/custom-drawn UIs, some web pages, remote desktops), Jarvis falls back to **Claude computer use**. Claude looks at screenshots and drives the real mouse and keyboard.

This matches the README's priority order: plugin API → COM → cloud API → UIA → **vision (computer use) as a last resort**. Computer use is the fallback. It doesn't replace UIA.

## Where we started (before this branch)

| Piece | State | File |
|---|---|---|
| Claude tool loop | Works, custom tools only | `windows-mvp/jarvis/brain.py` |
| `screenshot` tool | Custom tool. Full-screen grab, `thumbnail(1600)`, no coordinate mapping | `windows-mvp/jarvis/tools/system.py` |
| Mouse/keyboard by coordinates | **Missing.** `send_keys` and the UIA `Click()` fallback only target named controls | `windows-mvp/jarvis/tools/uia.py` |
| DPI awareness | **Missing.** Screenshots and cursor coordinates disagree on scaled displays (125%/150%) | none |
| Model | `claude-opus-5-5` with server-side fallbacks on | `windows-mvp/jarvis/config.py` |
| SDK | `anthropic>=1.13`. 1.13 already parses the computer toolset blocks (`toolset_name`, `ComputerToolUseBlock`) | `windows-mvp/requirements.txt` |


## API facts that set the design

1. **On `claude-opus-5-5`, the only computer-use form is the toolset.** Use `{"type": "computer_toolset_20260801"}` with no beta header, no `name` and no display size. The old `computer_20251124` / `computer-use-2025-11-24` form **returns a 400** on this model through the Claude API. Most online examples still use the old form, so don't copy them.
2. **Each action is its own `tool_use` block.** The block's `name` is the action (`screenshot`, `left_click`, `type`, `key`, `scroll`, `zoom`, ...) and it carries `"toolset_name": "computer"`. The action is **not** in `input.action`.
3. **There are 17 members:** `screenshot`, `zoom` (`region [x0,y0,x1,y1]`), `left_click` / `right_click` / `middle_click` / `double_click` / `triple_click` (`coordinate?`, `text?` modifiers), `left_click_drag` (`start_coordinate`, `coordinate`), `mouse_move`, `left_mouse_down`, `left_mouse_up`, `cursor_position`, `scroll` (`scroll_direction`, `scroll_amount`, `coordinate?`, `text?`), `type` (`text`), `key` (`text` like `"Return"`, `"ctrl+s"`, `repeat?`), `hold_key` (`text`, `duration`), `wait` (`duration`). Any of them can be turned off with `configs`, for example `{"zoom": {"enabled": false}}`.
4. **Claude can send several actions in one turn.** Run them in order and stop at the first failure. Every later block in that turn gets `is_error: true` with the exact text `Not executed: an earlier computer action in this turn failed.`
5. **Every `tool_result` must echo `"toolset_name": "computer"`.** The API rejects a result without it. Only `screenshot` and `zoom` return an image. The others can return `"OK"`.
6. **The API doesn't resize screenshots, so we have to.** The limit is 2576 px on the long edge and about 3.75 MP. Once a request holds more than 20 images, each side must be 2000 px or less. Aim for **about 1280×720**. Claude's coordinates are in **our screenshot's pixel space**, so we map them back to real screen pixels before clicking.
7. **Never remove old screenshots from history.** On Opus 5.5, editing earlier turns invalidates the thinking blocks that come after them ("preserved thinking"). Keep screenshots small instead. Our `reset()` already starts a fresh conversation, and that's fine.
8. **A name clash breaks the current code.** Toolset blocks are still `type: "tool_use"`, so today's loop would send `screenshot` to our custom `screenshot` tool, which returns an unscaled image and no `toolset_name`, and the request fails. Every other action would come back "Unknown tool". The custom `screenshot` tool must be renamed or removed while the toolset is on.

## Status

**Built on this branch (`eshaan-work`):**

| Step | Done | Where |
|---|---|---|
| 1 Input and capture | ✅ DPI awareness at startup. Primary-monitor capture resized to 1280 px on the long edge, with a `Geometry` class that maps screenshot↔screen coordinates. `SendInput` mouse, wheel and Unicode typing. xdotool key names → VK codes. All 17 members, **including zoom** | `jarvis/tools/wininput.py` (Windows calls), `jarvis/tools/computer.py` (logic) |
| 2 Brain wiring | ✅ Toolset added when `JARVIS_COMPUTER_USE` is on (**default on**, `0` turns it off). Custom `screenshot` tool dropped while it's on. Toolset calls run in order with halt-on-failure. `toolset_name` on every result. Step limit raised to 40 (`JARVIS_MAX_STEPS`). System prompt keeps computer use as the last resort | `jarvis/brain.py`, `jarvis/config.py` |
| 3 Safety | ✅ Risky-click confirmation (UIA control under the cursor checked against Send/Delete/Buy…). Password fields refused for both `type` and `ui_set_text`. Stops on lock screen/UAC. Corner kill switch. Every action in the audit log with real coordinates and the control clicked | `jarvis/tools/computer.py`, `jarvis/tools/uia.py` |
| 4 Tests | ✅ Offline: scaling, key mapping, every action against a fake screen, the safety checks, and a brain batch with halt-on-failure (`tests/test_core.py`). Windows CI: click and type into Notepad by position, then read it back through UIA (`tests/smoke_windows.py`) | `tests/` |
| 5 Docs and settings | ✅ `.env.example`, MVP README, this plan, plus [CREDENTIALS_AND_USER_SECURITY.md](CREDENTIALS_AND_USER_SECURITY.md) | |

**Next:**
- [ ] Manual end-to-end run with a real API key. Paint's canvas exposes nothing to UIA, so it's the target case: `jarvis.bat --do "open Paint and draw a square"`. Also try one browser task and one game or launcher.
- [ ] Tune `effort` for computer-use turns. Jarvis defaults to `low` for fast voice replies, but multi-step GUI work may do better at `medium`, the model's own default. If so, raise effort only for turns that use the toolset (per-message effort).
- [ ] Measure cost and latency per step. Try a 1024 px long edge and compare accuracy.
- [ ] Multi-monitor support: capture the monitor the target window is on, and handle virtual-desktop offsets.
- [ ] A global hotkey and an on-screen "Jarvis is driving" overlay with a Stop button (see the user-security plan).
- [ ] Ask before pressing Enter in chat and email apps. A `key Return` in a message box sends the message, and the risky-click check doesn't cover that yet.
- [ ] User-side credential fill and other user-end security (planned, not the current focus): [CREDENTIALS_AND_USER_SECURITY.md](CREDENTIALS_AND_USER_SECURITY.md).

## Known limits (same as UIA, by design)
- Elevated (admin) windows reject input from a non-elevated Jarvis (UIPI).
- The lock screen, UAC and Ctrl+Alt+Del are off-limits. Jarvis stops and says so.
- DRM video shows up black in screenshots.
- Primary monitor only, for now.
- Computer use is slower and costs more than UIA (one screenshot per step), so the prompt keeps it as a fallback.
- Passwords are never typed. Planned user-approved fill: [CREDENTIALS_AND_USER_SECURITY.md](CREDENTIALS_AND_USER_SECURITY.md).
