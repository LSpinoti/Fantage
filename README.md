# Stairies bot (Fantage Project)

Plays the Stairies minigame automatically: navigates to the Enchanted Grotto,
starts a game, plays it with an exact look-ahead planner, collects the gem and repeats.

## Setup

**macOS:** `./setup_mac.sh`, then put your login in `.env` (`EMAIL=...`, `PASSWORD=...`).

**Windows:** in PowerShell, `powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1`,
then `notepad .env` and run `.venv\Scripts\python stairies_bot.py --headless`. The faster C++
planner needs MinGW-w64 `g++` on PATH; without it the Python planner is used.

**Linux:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m playwright install chromium`,
then create `.env` as above (`chmod 600 .env`).

## Run

```bash
.venv/bin/python stairies_bot.py --headless --end-level 8      # hidden browser (recommended)
.venv/bin/python stairies_bot.py --visible --end-level 8       # same, window shown
caffeinate -i .venv/bin/python stairies_bot.py --headless --end-level 8   # macOS overnight
```

Stop with Ctrl+C. Useful options: `--games N`, `--end-score N`, `--no-gem-exit`,
`--min-react S` (default 0.5 s; the server silently rejects longer runs played faster), `-v`.

`python3 games_report.py` summarises `logs/games.csv` (coins/min, target gems/hour);
`python3 gems.py stats` shows gem totals.

## How it works

- `gamestate.py` exposes the game's own state in the bot's browser (read-only);
  `stariesgame.py` is an exact port of the game logic, `cpp/staries.cpp` +
  `cplanner.py` the fast planner (built automatically; `planner.py` is the Python fallback).
- Menus, dialogs and gems are recognised from reference images in `refs/`.
- `fio.py` (on-screen KDE/Wayland capture + uinput mouse) is only used without
  `--headless`/`--visible` and only works on Linux/KDE.
