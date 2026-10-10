# Notes for Claude agents working in this repo

- The runnable code is `windows-mvp/` (Windows-only Python). The plan is in `README.md`.
- **Testing follows one shared spec:** `windows-mvp/TESTING.md`. Follow it; don't invent your own setup.
  - Cloud sessions run Linux, so only the unit tests run here: `cd windows-mvp && python -m pytest -q tests/test_core.py`. They must pass before you push.
  - Windows behaviour is verified by the `windows-mvp` CI job (pinned `windows-2025` image, Python 3.12.10, `requirements.lock.txt`). Treat CI green as the gate. Never call a change "tested on Windows" unless that job passed on your commit.
  - `python -m jarvis --doctor` is expected to fail on Linux. That's fine; don't "fix" the spec to make it pass.
- Don't change `requirements.lock.txt`, the Python version or `TEST_PROFILE` in `jarvis/doctor.py` on a feature branch. Spec changes go in their own PR with a `SPEC_VERSION` bump.
- New tools go in `windows-mvp/jarvis/tools/` with a risk tier. Anything that sends, deletes, buys or posts must be `confirm` or go through `confirm_if`. Add a unit test, and add a smoke-test step if it can run headless on Windows.
- Fill in the PR template's testing section, including the CI doctor fingerprint.
