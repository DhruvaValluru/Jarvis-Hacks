#!/bin/sh
# Mac/Linux: installs what's needed the first time, starts Jarvis and opens the page.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (takes a minute)..."
  python3 -m venv .venv || { echo "Python 3.10 or newer is needed: https://www.python.org/downloads/"; exit 1; }
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { echo "Installing requirements failed (see above)."; exit 1; }
exec .venv/bin/python server.py --open
