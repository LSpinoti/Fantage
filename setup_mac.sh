#!/bin/bash
# One-time setup on macOS: venv, dependencies, Playwright's Chromium, C++ planner.
set -e
cd "$(dirname "$0")"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Install the Xcode Command Line Tools first (needed for the C++ planner):  xcode-select --install"
  exit 1
fi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -c "import cplanner; print('C++ planner:', 'ready' if cplanner.available() else 'unavailable (Python fallback will be used)')"
if [ ! -f .env ]; then
  printf 'EMAIL=\nPASSWORD=\n' > .env
  echo "Created .env - put your Fantage login in it."
fi
chmod 600 .env
echo
echo "Run (keeps the Mac awake while the bot runs):"
echo "  caffeinate -i .venv/bin/python stairies_bot.py --headless --end-level 8"
