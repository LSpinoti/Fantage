#!/usr/bin/env python3
"""Deterministic Stairies bot (Fantage, Chromium on the left/laptop screen).

Loop:  lobby -> click "Stairies" sign -> YES -> wait for load -> PLAY
       -> play with the solver until the gem icon turns purple (gem earned),
          then click the lock ("Click Here to End the Game"), or until
          "no more moves" -> EXIT
       -> CLOSE the gem dialog if it appears -> repeat.

Screen states are recognised by comparing small reference patches (refs/*.png)
at fixed positions; the board is read from fixed star-centre pixels.
No ML / LLM involved.

Usage:  python3 stairies_bot.py [--games N] [--verbose]          # on the real screen
        .venv/bin/python stairies_bot.py --headless [--games N]    # hidden browser
        .venv/bin/python stairies_bot.py --visible [--games N]     # same, browser window shown
Stop:   Ctrl+C in the terminal (the virtual mouse button is released on exit).
"""
import argparse
import json
import signal
import os
import time

import numpy as np
from PIL import Image

import fio
import gems
import cplanner
import planner
import score
import solver
import stariesgame
import vision

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, 'refs')
LOG_DIR = os.path.join(HERE, 'logs')

# name: (game x, game y) top-left of the reference patch
REFS = {
    'lobby': (575, 108),
    'dialog': (300, 262),
    'title': (392, 412),
    'gameover': (330, 160),
    'gameover_exit': (380, 358),
    'gem': (300, 155),
    'gem_close': (360, 360),
    # navigation (lobby = inside the Enchanted Grotto with the Stairies sign)
    'server': (307, 22),           # "SELECT A SERVER" title
    'map': (232, 408),             # world map "Welcome to a Fantastic Age!" banner
    'grotto_outside': (615, 192),  # "Enchanted Grotto" sign outside the door
    'toolbar_map': (92, 482),      # map button on the in-room toolbar
}
MATCH_THRESHOLD = 12.0   # mean abs RGB difference; matches score ~0-2, others >19

# Click targets (game coords)
CLICK = {
    'stairies_sign': (610, 122),
    'yes': (355, 306),
    'play': (420, 440),
    'exit': (420, 371),
    'gem_close': (416, 370),
    'end_game': (588, 488),   # lock "Click Here to End the Game"
    'map_button': (113, 503),
    'map_wizard': (697, 205), # wizard / grotto area on the world map
    'grotto_door': (675, 262),
}
# Points (excluding bonuses) needed to finish a level, fitted from logged games
# (analyze.py): about 245 at level 1, rising ~75 per level.
def LEVEL_TARGET(level):
    return 170 + 75 * level


# Server list slots (game coords); a full server is skipped by trying the next.
SERVER_SLOTS = [(120, 89), (355, 89), (590, 89), (120, 128), (355, 128), (590, 128)]
SERVER_NAMES = ['Aqua Unicorn', 'Blue Tiger', 'Fuchsia Flamingo', 'Periwinkle Puffin', 'Pink Cow', 'Purple Panda']
# Population bars: orange fill starting at these game x, on rows y 90 / 130; ~108 px when full.
BAR_X0, BAR_Y, BAR_LEN = (174, 408, 642), (90, 130), 112
SERVER_RETRY_S = 600      # skip a server for this long after it was full

PARK = (830, 300)        # cursor rest position (page background, right of game)

_refs = {k: np.asarray(Image.open(os.path.join(REF_DIR, k + '.png')).convert('RGB'), dtype=np.int16)
         for k in REFS}


def matches(img, name):
    x, y = REFS[name]
    ref = _refs[name]
    h, w = ref.shape[:2]
    s = fio.SCALE
    patch = img[y * s:y * s + h, x * s:x * s + w].astype(np.int16)
    if patch.shape != ref.shape:
        return False
    return float(np.abs(patch - ref).mean()) < MATCH_THRESHOLD


def find(img, name, step=3):
    """Search the whole frame for a reference patch (used where the room
    scrolls). Returns (score, gx, gy) of the best match, game coords."""
    ref = _refs[name][::4, ::4]                 # quarter native res = half game res
    small = img[::4, ::4].astype(np.int16)
    h, w = ref.shape[:2]
    best = (1e9, 0, 0)
    for y in range(0, small.shape[0] - h, step):
        for x in range(0, small.shape[1] - w, step):
            d = np.abs(small[y:y + h, x:x + w] - ref).mean()
            if d < best[0]:
                best = (d, x, y)
    d, x, y = best
    best = (1e9, x * 4 / fio.SCALE, y * 4 / fio.SCALE)   # coarse hit in game coords
    # refine around the coarse hit at full res
    ref_full = _refs[name]
    H, W = ref_full.shape[:2]
    for yy in range(max(0, y * 4 - 12), y * 4 + 13, 2):
        for xx in range(max(0, x * 4 - 12), x * 4 + 13, 2):
            patch = img[yy:yy + H, xx:xx + W]
            if patch.shape != ref_full.shape:
                continue
            dd = float(np.abs(patch.astype(np.int16) - ref_full).mean())
            if dd < best[0]:
                best = (dd, xx / fio.SCALE, yy / fio.SCALE)
    return best


# Door click offset from the sign patch's top-left: the top of the door, just
# under the sign and above the heads of players idling in the doorway.
GROTTO_DOOR_FROM_SIGN = (60, 52)


def stars_shown(img):
    """True once the result dialog shows the server's 'Stars earned' value."""
    s = fio.SCALE
    p = img[238 * s:258 * s, 540 * s:630 * s]
    return int((p > 200).all(2).sum()) > 200


def server_fill(img):
    """Population of each server in the list, 0..1 (1 = full), from its orange bar."""
    s = fio.SCALE
    out = []
    for i in range(len(SERVER_SLOTS)):
        x0, y = BAR_X0[i % 3], BAR_Y[i // 3]
        p = img[(y - 3) * s:(y + 3) * s, x0 * s:(x0 + BAR_LEN) * s].astype(np.int16)
        R, G, B = p[..., 0], p[..., 1], p[..., 2]
        orange = (R > 200) & (G > 90) & (G < 210) & (B < 90)
        out.append(float(orange.any(0).mean()))
    return out


def modal_button(img):
    """If a white message box (e.g. 'Couldn't join ...: server is full' or
    'This room is full') is open, return the game coords of its blue button."""
    s = fio.SCALE
    c = img[200 * s:330 * s, 240 * s:600 * s]
    if ((c > 235).all(2)).mean() < 0.6:
        return None
    p = img[150 * s:420 * s, 200 * s:640 * s].astype(np.int16)
    R, G, B = p[..., 0], p[..., 1], p[..., 2]
    ys, xs = np.where((R < 90) & (G > 100) & (G < 170) & (B > 200))
    if len(xs) < 1000:
        return None
    return 200 + xs.mean() / s, 150 + ys.mean() / s


GEM_CAP_CLOSE = (409, 293)
CAPPED_END_LEVEL = 8      # after the daily gem cap: best coins/min in our experiments


def gem_cap_open(img):
    """'You have reached your gem cap for the day' box: bright-to-darker blue
    panel with a white border and a dark CLOSE button (checked by colour, so it
    works whatever fonts the platform renders)."""
    s = fio.SCALE
    p = img.astype(np.int16)
    R, G, B = p[..., 0], p[..., 1], p[..., 2]

    def frac(mask, x0, y0, x1, y1):
        return float(mask[y0 * s:y1 * s, x0 * s:x1 * s].mean())
    bright = (R < 80) & (G > 100) & (G < 190) & (B > 190)
    mid = (R < 100) & (G > 85) & (G < 135) & (B > 150) & (B < 215)
    white = (R > 235) & (G > 235) & (B > 235)
    button = (R < 70) & (G < 110) & (B > 110) & (B < 200)
    return (frac(bright, 210, 140, 610, 200) > 0.9 and frac(mid, 210, 318, 610, 365) > 0.8
            and frac(white, 192, 150, 194, 350) > 0.8 and frac(white, 626, 150, 628, 350) > 0.8
            and frac(button, 350, 283, 468, 303) > 0.25)


def popup_open(img):
    """A red round close button (e.g. another player's ID card) at top right."""
    s = fio.SCALE
    b = img[36 * s:64 * s, 771 * s:799 * s].reshape(-1, 3).astype(np.int16)
    R, G, B = b[:, 0], b[:, 1], b[:, 2]
    red = ((R > 190) & (G < 90) & (B < 90)).mean()
    white = ((R > 230) & (G > 230) & (B > 230)).mean()
    return red > 0.2 and white > 0.05
_last_grotto = None


def detect(img):
    """Return the current screen state name."""
    if matches(img, 'gem') or matches(img, 'gem_close'):
        return 'gem'
    if matches(img, 'gameover_exit'):
        return 'gameover'
    if modal_button(img) is not None:
        return 'modal'
    if matches(img, 'dialog'):
        return 'dialog'
    if matches(img, 'title'):
        return 'title'
    if gem_cap_open(img):
        return 'gemcap'
    if vision.board_visible(img):
        rows, locks = vision.read_board(img)
        if vision.board_complete(rows):
            return 'board'
    if matches(img, 'lobby'):
        return 'lobby'
    if matches(img, 'server'):
        return 'server'
    if matches(img, 'map'):
        return 'map'
    if matches(img, 'toolbar_map'):
        global _last_grotto
        if popup_open(img):
            return 'popup'
        match_d, gx, gy = find(img, 'grotto_outside')
        if match_d < 20:
            _last_grotto = (gx + GROTTO_DOOR_FROM_SIGN[0], gy + GROTTO_DOOR_FROM_SIGN[1])
            return 'grotto_outside'
        return 'room'
    return 'unknown'


class Bot:
    def __init__(self, verbose=False, gem_exit=True, state_reader=None, end_score=None, end_level=None,
                 tag='', min_react=0.0):
        self.mouse = fio.Mouse()
        self.min_react = min_react
        self.end_score, self.end_level = end_score, end_level
        if end_score or end_level:
            gem_exit = False                   # explicit stopping point replaces the gem exit
        self.tag = tag or ('gem-exit' if gem_exit else
                           f'score{end_score}' if end_score else f'level{end_level}' if end_level else 'full')
        self.game_start = None                 # wall time the current game began
        self.game_record = None                # finished game waiting for its gem result
        self.last_cycle_end = None
        header = 'end_time,tag,game,score,level,stars_est,gem,play_s,cycle_s,moves\n'
        gpath = os.path.join(LOG_DIR, 'games.csv')
        os.makedirs(LOG_DIR, exist_ok=True)
        new_g = not os.path.exists(gpath)
        self.gamelog = open(gpath, 'a')
        if new_g:
            self.gamelog.write(header)
        self.state_reader = state_reader   # live game state (headless), else None
        self.use_cpp = state_reader is not None and cplanner.available()
        if state_reader is not None:
            self.log('planner: ' + (f'C++ (depth {cplanner.DEPTH}, beam {cplanner.BEAM})' if self.use_cpp
                                    else 'Python (C++ build unavailable)'))
        self._st_now, self._st_seen = None, 0.0
        self.verbose = verbose
        self.gem_exit = gem_exit   # end the game as soon as the gem is earned
        self.games = 0
        self.moves = 0
        self.last_state = None
        self.state_since = time.time()
        self.failed = {}   # board signature -> set of moves that did nothing
        self.ended = False  # already clicked "end game" this game
        self.server_idx = None          # server we last tried to join
        self.server_failed = {}         # server index -> time it was full
        self.room_full = 0              # consecutive 'room is full' messages
        self.context = None             # last recognised screen before a message box
        self.gem_capped_day = self._load_state().get('gem_capped_day')   # date the daily gem cap was hit
        self.last_moved_on = None   # board we last made a move on
        self.last_move = None
        self.t_last_move = None
        self.drag_steps = 2
        os.makedirs(LOG_DIR, exist_ok=True)
        header = ('time,game,level,move,move_desc,score_before,points,score_after,pred_points,pred_chains,'
                  'pred_blasted,pred_supers,meter_top,locks,supers,gap_s,think_s,drag_s,board\n')
        path = os.path.join(LOG_DIR, 'moves.csv')
        if os.path.exists(path):
            with open(path) as f:
                if f.readline() != header:      # old format: keep it aside
                    os.replace(path, os.path.join(LOG_DIR, f'moves_old_{int(time.time())}.csv'))
        new = not os.path.exists(path)
        self.movelog = open(path, 'a')
        if new:
            # gap = time from the end of the previous move to this move's board settling
            self.movelog.write(header)
        self.pending = None     # CSV fields of the last move, completed when the board settles
        self.level = 1          # level within the current game (counted from level-up bonuses)
        self.level_points = 0   # move points scored so far in this level
        self.score = None       # last score read from the screen

    def log(self, *a):
        print(time.strftime('%H:%M:%S'), *a, flush=True)

    def click(self, name):
        self.mouse.click(*CLICK[name])
        self.mouse.move(*PARK)

    # ---- board handling -------------------------------------------------
    def wait_settled(self, timeout=5.0):
        """Wait until the board is at rest: fully readable, containing no
        cluster (the game would still clear it), different from the board we
        last moved on, and identical in two consecutive captures.
        Returns (rows, locks, img) or None if something else appeared."""
        prev = None
        t0 = time.time()
        while time.time() - t0 < timeout:
            img = fio.capture_play()
            rows, locks = vision.read_board(img)
            if vision.board_visible(img) and vision.board_complete(rows):
                cur = (tuple(rows), tuple(map(tuple, locks)))
                g = solver.make_grid(rows, locks)
                if (cur == prev and not solver.has_match(g)
                        and (cur != self.last_moved_on or time.time() - t0 > 0.8)):
                    return rows, locks, img
                prev = cur
            else:
                prev = None
                st = detect(fio.capture())
                if st in ('gameover', 'gem', 'lobby', 'title', 'dialog'):
                    return None
        return None

    def play_turn(self):
        b = self.wait_settled()
        if b is None:
            return
        t_settled = time.time()
        rows, locks, img = b
        sc = score.read_score(img)
        if sc is not None and self.score is not None and not (0 <= sc - self.score < 5000):
            sc = None                      # misread (e.g. overlay): ignore
        if sc is not None:
            self.finish_pending(sc)
            self.score = sc
        cur = (tuple(rows), tuple(map(tuple, locks)))
        sig = vision.fmt(rows, locks)
        # A move that left the board unchanged (e.g. misread lock): don't repeat it.
        if cur == self.last_moved_on and self.last_move is not None:
            self.failed.setdefault(sig, set()).add(self.last_move)
            self.log('move had no effect, excluding it:', self.last_move)
        if self.gem_exit and vision.gem_earned(img):
            if not self.ended:
                self.log(f'gem earned after {self.moves} moves -> ending game')
                self.click('end_game')
                self.ended = True
                time.sleep(1.0)
            return
        supers = vision.read_supers(img)
        meter = vision.meter_top(img)
        g = solver.make_grid(rows, locks, supers)
        move, move_value = solver.best_move(g, self.failed.get(sig, set()),
                                            remaining=self.remaining_points(meter))
        if move is None:
            # No legal move: the game will show "no more moves" + Game Over.
            if self.verbose:
                self.log('no legal move on', sig, '- waiting for game over')
            time.sleep(0.5)
            return
        t_think = time.time()
        (r1, c1), (r2, c2) = solver.drag_cells(move)
        x1, y1 = vision.cell_xy(r1, c1)
        x2, y2 = vision.cell_xy(r2, c2)
        self.mouse.drag(x1, y1, x2, y2, steps=self.drag_steps, step_delay=0.005, hold=0.01)
        self.mouse.move(*PARK)
        t_done = time.time()
        self.moves += 1
        self.last_moved_on, self.last_move = cur, move
        n_locks = sum(map(sum, locks))
        gap = t_settled - self.t_last_move if self.t_last_move else 0.0
        pred = solver.simulate(g, move)
        self.pending = dict(time=t_done, game=self.games + 1, level=self.level, move=self.moves, move_desc=move,
                            pred_points=pred.points if pred else '',
                            pred_chains='/'.join('+'.join(map(str, c)) for c in pred.chains) if pred else '',
                            pred_blasted=pred.blasted if pred else '', pred_supers=pred.supers if pred else '',
                            meter_top='' if meter is None else f'{meter:.1f}',
                            score_before=self.score, locks=n_locks, supers=sum(map(sum, supers)),
                            gap=gap, think=t_think - t_settled, drag=t_done - t_think,
                            board=sig.replace(' ', '') + (' super=' + ';'.join(
                                f'{r}{c}' for r in range(6) for c in range(6) if supers[r][c])
                                if any(map(any, supers)) else ''), sig=sig)
        self.t_last_move = t_done

    def finish_pending(self, score_after):
        """Complete the last move's record once the next board (score) is known."""
        p = self.pending
        if p is None:
            return
        self.pending = None
        before = p['score_before']
        pts = '' if (before is None or score_after is None) else score_after - before
        md = p['move_desc']
        self.movelog.write(f"{p['time']:.3f},{p['game']},{p['level']},{p['move']},{md[0]}{md[1]}{md[2]},"
                           f"{'' if before is None else before},"
                           f"{pts},{'' if score_after is None else score_after},{p['pred_points']},"
                           f"{p['pred_chains']},{p['pred_blasted']},{p['pred_supers']},{p['meter_top']},"
                           f"{p['locks']},{p['supers']},"
                           f"{p['gap']:.3f},{p['think']:.3f},{p['drag']:.3f},{p['board']}\n")
        self.movelog.flush()
        if pts != '' and pts >= 500:
            self.level += 1                # level-up bonus seen
            self.level_points = 0
        elif pts != '':
            self.level_points += pts
        if self.verbose:
            total = '?' if score_after is None else score_after
            gained = '?' if pts == '' else f'+{pts}'
            self.log(f"move {p['move']}: {p['move_desc']} points={gained} total={total} locks={p['locks']} "
                     f"wait={p['gap']:.2f}s think={p['think']:.3f}s drag={p['drag']:.2f}s board={p['sig']}")

    STATE_FILE = os.path.join(LOG_DIR, 'bot_state.json')

    def _load_state(self):
        try:
            with open(self.STATE_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, **kv):
        st = self._load_state()
        st.update(kv)
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(self.STATE_FILE, 'w') as f:
            json.dump(st, f)

    def capped_today(self):
        return self.gem_capped_day == time.strftime('%Y-%m-%d')

    def write_game(self, gem):
        r = self.game_record
        if r is None:
            return
        self.game_record = None
        cycle = r['end'] - self.last_cycle_end if self.last_cycle_end else ''
        self.last_cycle_end = r['end']
        stars = min(500, round(r['score'] / 40))
        play = f"{r['play']:.1f}" if r['play'] != '' else ''
        self.gamelog.write(f"{r['end']:.0f},{self.tag},{self.games},{r['score']},{r['level']},{stars},{gem},"
                           f"{play},{'' if cycle == '' else f'{cycle:.1f}'},{r['moves']}\n")
        self.gamelog.flush()

    def remaining_points(self, meter=None):
        """Estimated points still needed for the next level (None if the
        score hasn't been read yet). The meter is only logged: its reading
        saturates once the fill passes the star's white highlight band."""
        if self.score is None:
            return None
        return max(0.0, LEVEL_TARGET(self.level) - self.level_points)

    # ---- main loop ------------------------------------------------------
    # ---- state-driven play (headless: reads the game's own state) -------
    COLOUR_LETTER = 'RGBYCMWP'   # game colour index -> our letters

    def state_turn(self):
        """Play one move from the live game state. Returns False when no game
        is in progress (menus, dialogs, game over), so screen logic takes over."""
        try:
            s = self.state_reader()
        except Exception:
            return False
        if not s or s['phase'] == 'over':
            return False
        if s['now'] != self._st_now:
            self._st_now, self._st_seen = s['now'], time.time()
        elif time.time() - self._st_seen > 3:
            return False                       # stale object from a finished game
        if s['phase'] != 'play':
            time.sleep(0.02)                   # animation / intro / level clear
            return True
        if self.game_start is None:
            self.game_start = time.time()
        if not self.ended:
            reason = None
            capped = self.capped_today()
            if self.gem_exit and not capped:
                if s.get('gemScore') is not None:
                    if s['score'] >= s['gemScore']:
                        reason = f"gem earned (score {s['score']} >= {s['gemScore']})"
                elif self.moves % 3 == 0:
                    gx0, gy0, gx1, gy1 = vision.GEM_BOX
                    if vision.gem_earned(fio.capture_region_full(gx0, gy0, gx1 - gx0, gy1 - gy0)):
                        reason = 'gem earned'
            if self.end_score and s['score'] >= self.end_score:
                reason = f"score {s['score']} reached {self.end_score}"
            end_level = self.end_level or (CAPPED_END_LEVEL if self.gem_exit and capped else None)
            if end_level and s['level'] >= end_level:
                reason = f"level {s['level']} reached"
            if reason:
                self.log(f'{reason} after {self.moves} moves -> ending game')
                self.score, self.level = s['score'], s['level']
                self.click('end_game')
                self.ended = True
                return True
        if self.ended:
            time.sleep(0.05)
            return True
        if self.min_react and self.t_last_move:
            wait = self.min_react - (time.time() - self.t_last_move)
            if wait > 0:
                time.sleep(wait)             # human-like pause after the board settles
                s = self.state_reader()
                if not s or s['phase'] != 'play':
                    return True
        t_start = time.time()
        g = stariesgame.Game(s)
        t_move = s['now'] + 80
        move, value, _ = (cplanner.plan if self.use_cpp else planner.plan)(g, t0=t_move)
        if move is None:
            time.sleep(0.05)
            return True
        pred = g.copy()
        st = pred.slide(move, t=t_move)
        t_think = time.time()
        axis, index, steps = move
        desc = ('r' if axis == 0 else 'c', index, steps)
        (r1, c1), (r2, c2) = solver.drag_cells(desc)
        x1, y1 = vision.cell_xy(r1, c1)
        x2, y2 = vision.cell_xy(r2, c2)
        self.mouse.drag(x1, y1, x2, y2, steps=self.drag_steps, step_delay=0.005, hold=0.01)
        self.mouse.move(*PARK)
        t_done = time.time()
        # wait for the game logic to finish the move
        s2 = None
        while time.time() - t_done < 10:
            s2 = self.state_reader()
            if s2 is None:
                break
            if s2['rng'] != s['rng'] or s2['score'] != s['score']:
                if s2['phase'] in ('play', 'over', 'intro', 'clear', 'clearWait'):
                    break
            elif time.time() - t_done > 0.6 and s2['phase'] == 'play':
                self.log('move did not register, retrying:', desc)
                return True
            time.sleep(0.015)
        if s2 is None:
            return True
        self.moves += 1
        gained = s2['score'] - s['score']
        desync = s2['phase'] == 'play' and s2['score'] != pred.score
        n_locks = sum(st_[1] for col in s['board'] for st_ in col)
        n_bombs = sum(st_[2] for col in s['board'] for st_ in col)
        rows = [''.join((self.COLOUR_LETTER[s['board'][x][y][0]].lower() if s['board'][x][y][1]
                         else self.COLOUR_LETTER[s['board'][x][y][0]]) for x in range(6)) for y in range(6)]
        gap = t_start - self.t_last_move if self.t_last_move else 0.0
        self.movelog.write(f"{t_done:.3f},{self.games + 1},{s['level']},{self.moves},{desc[0]}{desc[1]}{desc[2]},"
                           f"{s['score']},{gained},{s2['score']},{st['points']},{st['steps']},,{st['bombs_used']},,"
                           f"{n_locks},{n_bombs},{gap:.3f},{t_think - t_start:.3f},{t_done - t_think:.3f},"
                           f"{''.join(rows)}\n")
        self.movelog.flush()
        self.t_last_move = time.time()
        self.score = s2['score']
        self.level = s2['level']
        if self.verbose or desync:
            self.log(f"move {self.moves}: {desc} lvl={s['level']} points=+{gained} total={s2['score']} "
                     f"locks={n_locks} bombs={n_bombs} think={t_think - t_start:.3f}s "
                     f"logic={time.time() - t_done:.2f}s" + ('  [prediction differed]' if desync else ''))
        return True

    def step(self):
        if self.state_reader is not None and self.state_turn():
            if self.last_state != 'board':
                self.last_state = 'board'
                self.state_since = time.time()
            return
        img = fio.capture()
        st = detect(img)
        if st not in ('modal', 'unknown'):
            self.context = st
        if st != self.last_state:
            if self.verbose or st in ('gameover', 'gem'):
                self.log('state:', st)
            self.last_state = st
            self.state_since = time.time()
        stuck = time.time() - self.state_since

        if (st in ('server', 'map', 'room', 'grotto_outside') and stuck > 60
                and self.reload is not None):
            self.log(f'stuck on {st} for 60s, reloading the game page')
            self.reload()
            self.state_since = time.time()
            return
        if st == 'gem':
            time.sleep(0.6)
            key, name = gems.classify(fio.capture())
            if key is None:
                self.log('gem dialog: could not read the gem name')
            else:
                counts = gems.record(key)
                self.log(f'gem: {name}  | totals: {gems.summary(counts)}')
            self.write_game(name or '?')
            self.click('gem_close')
            time.sleep(1.0)
        elif st == 'gameover':
            # Wait for the server's verdict (the "Stars earned" value) before leaving:
            # clicking EXIT too early loses the run's stars and gem.
            t_wait = time.time()
            got = False
            while time.time() - t_wait < 20:
                dlg = fio.capture()
                if not matches(dlg, 'gameover_exit'):
                    break
                if stars_shown(dlg):
                    got = True
                    break
                time.sleep(0.2)
            self.log(f'server result {"arrived" if got else "NOT shown"} after {time.time() - t_wait:.1f}s')
            self.games += 1
            self.finish_pending(None)
            self.log(f'game {self.games} over after {self.moves} moves, '
                     f'score {self.score if self.score is not None else "?"} (last read)')
            now = time.time()
            sc = self.score or 0
            self.game_record = dict(end=now, score=sc, level=self.level, moves=self.moves,
                                    play=(now - self.game_start) if self.game_start else '')
            self.game_start = None
            self.moves = 0
            self.failed.clear()
            self.ended = False
            self.last_moved_on = self.last_move = self.t_last_move = None
            self.score = None
            self.level = 1
            self.level_points = 0
            self.click('exit')
            time.sleep(1.5)
        elif st == 'dialog':
            self.click('yes')
            time.sleep(1.0)
        elif st == 'title':
            time.sleep(0.3)
            self.click('play')
            time.sleep(1.0)
        elif st == 'board':
            self.play_turn()
        elif st == 'lobby':
            self.room_full = 0
            if self.game_record is not None:
                self.write_game('capped' if self.capped_today() else '')   # ended without a gem
            if self.max_games is not None and self.games >= self.max_games:
                raise SystemExit
            self.click('stairies_sign')
            time.sleep(1.2)
        elif st == 'gemcap':
            if not self.capped_today():
                self.gem_capped_day = time.strftime('%Y-%m-%d')
                self._save_state(gem_capped_day=self.gem_capped_day)
                mode = (f'playing to level {CAPPED_END_LEVEL} for coins' if self.gem_exit and not
                        (self.end_score or self.end_level) else 'continuing for coins')
                self.log(f'daily gem cap reached - no more gems today; {mode}')
            self.mouse.click(*GEM_CAP_CLOSE)
            self.mouse.move(*PARK)
            time.sleep(1.0)
        elif st == 'popup':
            self.mouse.click(785, 50)          # close another player's card etc.
            self.mouse.move(*PARK)
            time.sleep(1.0)
        elif st == 'server':
            time.sleep(1.0)
            img = fio.capture()
            fill = server_fill(img)
            now = time.time()
            ok = [i for i in range(len(SERVER_SLOTS))
                  if fill[i] < 0.95 and now - self.server_failed.get(i, 0) > SERVER_RETRY_S]
            if not ok:                         # everything full/failed: forget failures, take the emptiest
                self.server_failed.clear()
                ok = list(range(len(SERVER_SLOTS)))
            i = min(ok, key=lambda k: fill[k])
            self.server_idx = i
            self.log(f'joining {SERVER_NAMES[i]} ({fill[i]:.0%} full; '
                     + ', '.join(f'{SERVER_NAMES[k].split()[0]} {fill[k]:.0%}' for k in range(len(fill))) + ')')
            self.mouse.click(*SERVER_SLOTS[i])
            self.mouse.move(*PARK)
            time.sleep(5.0)
        elif st == 'modal':
            btn = modal_button(img)
            if self.context == 'server':
                if self.server_idx is not None:
                    self.server_failed[self.server_idx] = time.time()
                    self.log(f'{SERVER_NAMES[self.server_idx]} is full, picking another server')
                else:
                    self.log('server message box, closing it')
                self.mouse.click(*btn)
                self.mouse.move(*PARK)
                time.sleep(1.5)
            else:
                self.room_full += 1
                self.log(f'room is full (message box, {self.room_full} in a row)')
                self.mouse.click(*btn)
                self.mouse.move(*PARK)
                if self.room_full >= 2 and self.reload is not None:
                    if self.server_idx is not None:
                        self.server_failed[self.server_idx] = time.time()
                    self.log('switching server')
                    self.room_full = 0
                    self.reload()              # back to the server list
                else:
                    time.sleep(15)             # wait a bit, then the normal flow retries the door
            self.state_since = time.time()
        elif st == 'map':
            time.sleep(1.0)
            self.click('map_wizard')
            time.sleep(4.0)
        elif st == 'grotto_outside':
            time.sleep(1.0)
            self.mouse.click(*_last_grotto)
            self.mouse.move(*PARK)
            time.sleep(5.0)
        elif st == 'room':
            # Some other room, or the Grotto with the sign briefly hidden by a
            # chat bubble: give it a few seconds before travelling via the map.
            if stuck > 4:
                self.click('map_button')
                time.sleep(2.5)
                self.state_since = time.time()
            else:
                time.sleep(0.3)
        else:
            # Loading screens, level splash, combo animations...
            if stuck > 30:
                path = os.path.join(LOG_DIR, f'unknown_{int(time.time())}.png')
                Image.fromarray(img).save(path)
                self.log('unknown screen for 30s, saved', path)
                self.state_since = time.time()
                if self.reload is not None:
                    self.log('reloading the game page')
                    self.reload()
                elif self.unknown_strikes >= 3:
                    raise SystemExit('Game not found on screen; stopping.')
                self.unknown_strikes += 1
            time.sleep(0.2)
        if st != 'unknown':
            self.unknown_strikes = 0

    def run(self, max_games=None, reload=None):
        self.max_games = max_games
        self.reload = reload
        self.unknown_strikes = 0
        self.mouse.move(*PARK)
        try:
            while True:
                self.step()
        except (KeyboardInterrupt, SystemExit) as e:
            if isinstance(e, SystemExit) and e.code:
                self.log(e.code)
        finally:
            self.mouse.button(False)
            self.mouse.close()
            self.log(f'stopped; {self.games} games played')


def _sigterm(signum, frame):
    raise KeyboardInterrupt      # clean shutdown (closes the browser) on kill/timeout too


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--games', type=int, default=None, help='stop after N games (default: forever)')
    ap.add_argument('--verbose', '-v', action='store_true')
    ap.add_argument('--no-gem-exit', action='store_true',
                    help="don't end the game when the gem is earned; play until no moves are left")
    ap.add_argument('--headless', action='store_true',
                    help='run in a hidden browser (needs: .venv/bin/python headless.py login, once)')
    ap.add_argument('--end-score', type=int, default=None,
                    help='end each game once the score reaches N (instead of at the gem)')
    ap.add_argument('--end-level', type=int, default=None,
                    help='end each game once level N is reached (instead of at the gem)')
    ap.add_argument('--tag', default='', help='label for this run in logs/games.csv')
    ap.add_argument('--min-react', type=float, default=0.5,
                    help='minimum seconds between the board settling and the next move (default 0.5). '
                         'The server silently rejects longer runs played at full bot speed.')
    ap.add_argument('--visible', action='store_true',
                    help='like --headless (own browser, game-state play, your mouse untouched) '
                         'but with the browser window shown')
    ap.add_argument('--screen-only', action='store_true',
                    help='headless/visible: play from screen reading only, not from the game state')
    a = ap.parse_args()
    if not (a.headless or a.visible):
        Bot(verbose=a.verbose, gem_exit=not a.no_gem_exit, end_score=a.end_score, end_level=a.end_level, tag=a.tag, min_react=a.min_react).run(a.games)
        return
    from playwright.sync_api import sync_playwright
    import headless
    import gamestate
    with sync_playwright() as pw:
        ctx, page = headless.launch(pw, headless=not a.visible)

        def on_console(msg):
            text = msg.text
            if msg.type in ('warning', 'error') and any(k in text.lower() for k in ('shard', 'staries', 'minigame')):
                print(time.strftime('%H:%M:%S'), f'[browser {msg.type}] {text[:300]}', flush=True)
        page.on('console', on_console)
        page.on('pageerror', lambda e: print(time.strftime('%H:%M:%S'), f'[page error] {str(e)[:300]}', flush=True))
        if not a.screen_only:
            gamestate.install(ctx)

        def reload():
            for attempt in range(6):            # ride out brief network/site hiccups
                try:
                    page.goto(headless.GAME_URL, wait_until='domcontentloaded')
                    break
                except Exception as e:
                    wait = 5 * (attempt + 1)
                    print(time.strftime('%H:%M:%S'), f'page load failed ({str(e).splitlines()[0][:80]}); '
                          f'retrying in {wait}s', flush=True)
                    time.sleep(wait)
            else:
                raise SystemExit('Could not load the game page.')
            page.wait_for_timeout(8000)  # logged-out redirect can take >5 s
            if '/game' not in page.url:   # site redirects to the homepage when logged out
                print(time.strftime('%H:%M:%S'), 'logged out; logging in with .env credentials', flush=True)
                try:
                    ok = headless.auto_login(page)
                except Exception as e:
                    print('auto-login failed:', type(e).__name__, flush=True)
                    ok = False
                if not ok:
                    raise SystemExit('Logged out of Fantage and auto-login failed. Check EMAIL/PASSWORD in .env, '
                                     'or run:  .venv/bin/python headless.py login')

        reload()

        if a.visible:
            dpr = page.evaluate('devicePixelRatio')
            if dpr != 2:
                print(time.strftime('%H:%M:%S'), f'warning: browser window scale is {dpr}, expected 2. '
                      'Move the window to the laptop screen, or use --headless.', flush=True)
            fio.set_backend(headless.HeadlessIO(page, native=True))
        else:
            fio.set_backend(headless.HeadlessIO(page, headless.GAME_ORIGIN))
        try:
            reader = None if a.screen_only else (lambda: gamestate.read(page))
            Bot(verbose=a.verbose, gem_exit=not a.no_gem_exit, end_score=a.end_score, end_level=a.end_level, tag=a.tag, min_react=a.min_react, state_reader=reader).run(a.games, reload=reload)
        finally:
            ctx.close()


if __name__ == '__main__':
    main()
