#!/bin/sh
# Mac/Linux: installs what's needed the first time, starts Jarvis and opens the page.
cd "$(dirname "$0")" || exit 1

new_enough() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }

# A setup left over from an older Python won't work; start it again.
if [ -x .venv/bin/python ] && ! new_enough .venv/bin/python; then
  rm -rf .venv
fi

if [ ! -x .venv/bin/python ]; then
  PY=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && new_enough "$candidate"; then
      PY="$candidate"
      break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Jarvis needs Python 3.10 or newer, and this computer doesn't have it yet."
    echo "Install it from https://www.python.org/downloads/ (the big yellow button),"
    echo "then run this again:  sh start.sh"
    exit 1
  fi
  echo "First run: setting up with $PY (takes a minute)..."
  "$PY" -m venv .venv || exit 1
fi

.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { echo "Installing requirements failed (see above)."; exit 1; }
exec .venv/bin/python server.py --open
