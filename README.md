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
.venv/bin/python stairies_bot.py --headless --end-level 8      # best for Moonstones/Black Diamonds and coins
.venv/bin/python stairies_bot.py --headless                    # default: stop at the gem (most gems per hour)
.venv/bin/python stairies_bot.py --visible --end-level 8       # same, browser window shown
caffeinate -i .venv/bin/python stairies_bot.py --headless --end-level 8   # macOS overnight
```

By default each game ends as soon as the gem is earned; once the daily gem cap
is hit, the default switches to playing to level 8 for coins until the next day.

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

## Findings

What we learned while building this, for anyone picking it up later.
Numbers come from our own logged games; sample sizes are small where noted.

### How the game works (read from its JavaScript)

- Fantage Project is an HTML5 game. Stairies' logic lives in `StariesScene-*.js`;
  its randomness comes from `MinigameRng-*.js` (Mulberry32), seeded by the server.
- 6x6 board, 8 colours. Dragging slides a whole row/column cyclically. A move is
  accepted only if it creates a 4-connected same-colour group of 3+; otherwise it snaps back.
- **Scoring:** each group scores `10 + 5 x (size - 3)`; every extra group cleared in
  the same step adds +10. There is no chain multiplier.
- **Super stars ("bombs")** are not random: any group of **5+** leaves one of its
  stars behind as a bomb. A bomb in a later group blasts its 3x3 neighbourhood
  (chaining into other bombs). Blasted stars count toward the level but score nothing.
- **Level progress counts stars broken, not points:** `70 + 20 x (level - 1)` per
  level. Clearing a level pays **+1000** and wipes all locks.
- **Locks** start at level 3 and run on a timer: one every `20 s x 0.8^(level - 3)`
  (20 s at level 3, ~4 s at level 10, ~1.4 s at level 15), applied when a move
  finishes. A row/column containing a lock can't be dragged; locked stars still
  match. The game ends when no legal move is left - so slow play means more locks.
- The client sends only the timed inputs; **the server re-plays them from the seed
  and computes the score itself**, so scores can't be faked.

### How the bot plays

- Early versions read the board from screenshots and guessed at refills
  (~24-30k per full game). The current one reads the game's own state and simulates
  every move exactly, including future refills, bombs and lock positions,
  searching 8 moves ahead in C++. A full game reached **39k** (the leaderboard top
  was 38k at the time); simulated average ~41k.
- **Play at human speed.** Long runs played at full bot speed (moving ~0.1 s after
  the board settles) were **silently rejected by the server**: no stars, no gem.
  Short runs usually got through. A 0.5 s pause before each move fixed it
  (`--min-react`, default 0.5).

### Rewards

- **Coins ("stars")** are roughly score / 40, **capped at 500 per game** (~20k points).
- **One gem per game**, given once the score passes a threshold (4,000 on our account).
- **Daily gem cap: 150.** After that the game shows "You have reached your gem cap
  for the day" - coins are still awarded.
- Gem types: Rare Sapphire, Very Rare Ruby, Ultra Rare Emerald, Legendary Diamond,
  Otherworldly Moonstone, Mythical Black Diamond.

### Experiment: when to stop each game (with the 0.5 s pause)

| Stop at | Games | Coins/game | Cycle | Coins/min | Moonstone + Black Diamond | per hour |
|---|---|---|---|---|---|---|
| Gem (~level 4, 4,000 pts) | 20 | 109 | 57 s | 115 | 10% | 6.4 |
| **Level 8** | 17 | 284 | 137 s | **124** | 35% | **9.3** |
| Level 12 | 9 | 492 | 246 s | 120 | 56% | 8.1 |

- **Rarer gems get more likely the further you get**: Moonstone + Black Diamond
  went from 10% at the gem threshold to 35% at level 8 and 56% at level 12.
- Because there's only one gem per game, **level 8 gives the most Moonstones and
  Black Diamonds per hour**, and also slightly the most coins per minute.
- Coins per minute are nearly flat (115-124) whatever you choose; past ~level 12 a
  game hits the 500-coin cap, so going longer only costs time.
- If you just want **as many gems as possible** (e.g. to reach the 150 cap fast),
  stopping at the gem gives ~63 gems/hour against ~26 at level 8.
- Small samples (9-20 games each): treat the level 8 vs 12 difference as a hint.

### Practical gotchas

- **One bot per account.** A second login (another machine, or the game open in
  your normal browser) logs the other session out; the bot then re-logs in from `.env`.
- Servers can be full ("Couldn't join ... server is full") and so can the Grotto
  ("This room is full"): the bot picks the emptiest server from the population bars
  and switches when needed.
- Players idling in the Grotto doorway can block the door click (it opens their ID
  card); the bot clicks the top of the door and closes such cards.
- The bot's browser profile has its own login cookies; they don't transfer between
  operating systems, which is why `.env` auto-login exists.
- Screen checks that match text (dialogs, gem names) may need re-capturing on a new
  OS because fonts render slightly differently; colour-based checks (popups, gem cap) don't.
- `logs/` keeps per-move (`moves.csv`) and per-game (`games.csv`) data, gem history,
  and screenshots of any screen the bot didn't recognise (`unknown_*.png`).
