#!/usr/bin/env python3
"""Bullseye bot (Fantage): plays perfect games (60 bullseyes = 600 points).

How the game scores (from BullseyeScene-*.js):
  hold the mouse for `hold` ms, release at mouse x.  With cross = 3000, 2600,
  2200, 1800, 1600, 1400, 1200... ms for levels 1..10, the target ends up at
      targetX = 1220 + floor(-1600 * (hold + 400) / cross + 0.5)
  and a shot is a bullseye (10 pts) when |targetX - x| < 8.
There is no randomness, so the only difficulty is timing (+-5 ms at level 7+).
The bot reads the game's clock right after pressing, then releases at the
exact moment that gives the planned hold, with the mouse already on the spot
where the target will be. Release latency is learned from each shot.

Runs in the hidden browser of the Stairies bot (same profile / .env login):
    cd ~/Desktop/Fantage && .venv/bin/python bullseye/bullseye_bot.py
Stops after a 600 game has been submitted (or --games attempts). Ctrl+C to stop.
"""
import argparse
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fio            # noqa: E402
import headless       # noqa: E402
import stairies_bot   # noqa: E402  (screen detection + room navigation)

CROSS = [3000, 2600, 2200, 1800, 1600, 1400, 1200, 1200, 1200, 1200]

# Click targets, game coords (same framing as the Stairies bot)
BULLSEYE_SIGN = (597, 222)
PLAY_BTN = (425, 407)
START_BTN = (420, 401)
RESULTS_OK = (405, 455)      # "OK!" on the final results scroll

_PATCH = re.compile(r"(\(this\['label'\]='Bullseye',)")


def install_patch(ctx):
    """Expose the Bullseye scene object as globalThis.__bull (read-only use)."""
    def handle(route):
        resp = route.fetch()
        body = resp.text()
        patched, n = _PATCH.subn(r"(globalThis.__bull=this,this['label']='Bullseye',", body, count=1)
        if not n:
            print('WARNING: Bullseye script changed; could not expose the game state', flush=True)
        route.fulfill(response=resp, body=patched if n else body,
                      headers={**resp.headers, 'cache-control': 'no-store'})
    ctx.route(re.compile(r'.*/assets/BullseyeScene-[^/]*\.js$'), handle)


READ = """() => {
  const g = globalThis.__bull;
  if (!g || g.destroyed) return null;
  const s = g.screen, out = {kind: s.kind, panel: !!g.panel, pointerX: g.pointerX, now: performance.now()};
  if (s.kind === 'playing') {
    const p = s.play;
    Object.assign(out, {turn: p.turn.kind, pressedAt: p.turn.pressedAt ?? null, clock: p.clock,
      moved: g.clockMovedAt, level: p.game.level, turns: [...p.game.turns], over: p.game.over,
      scores: [...p.game.levelScores], inputs: p.inputs.length ? p.inputs[p.inputs.length - 1] : null});
  }
  return out;
}"""


def target_x(hold, cross):
    t = min(hold + 400, cross)
    return 1220 + math.floor(-1600 * t / cross + 0.5)


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


class BullseyeBot:
    def __init__(self, page, io):
        self.page, self.io = page, io
        self.cdp = io.cdp
        self.latency = 0.0        # ms between our release call and the event's game time
        self.sx = None            # stage x = sx[0] * game_x + sx[1]
        self.offset = None        # page performance.now() - our perf_counter (ms)

    def read(self):
        return self.page.evaluate(READ)

    # --- clocks / coordinates ------------------------------------------------
    def sync_clock(self):
        best = None
        for _ in range(15):
            t0 = time.perf_counter() * 1000
            now = self.page.evaluate('performance.now()')
            t1 = time.perf_counter() * 1000
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, now - (t0 + t1) / 2)
        self.offset = best[1]

    def calibrate_x(self):
        pts = []
        for gx in (150, 650):
            self.io.move(gx, 250)
            time.sleep(0.15)
            pts.append((gx, self.read()['pointerX']))
        (a, pa), (b, pb) = pts
        k = (pb - pa) / (b - a)
        self.sx = (k, pa - k * a)
        log(f'stage x = {k:.4f} * game x + {self.sx[1]:.2f}')

    def game_x(self, stage_x):
        k, c = self.sx
        return (stage_x - c) / k

    # --- one shot ---------------------------------------------------------------
    def shoot(self, st):
        level = st['level']
        cross = CROSS[level - 1]
        hold = round(0.5125 * cross) - 400        # target lands mid-range
        x = target_x(hold, cross)
        self.io.move(self.game_x(x), 250)
        time.sleep(0.05)
        self.sync_clock()
        self.io.button(True)
        for _ in range(200):
            s = self.read()
            if s and s.get('turn') == 'held':
                break
        else:
            log('press did not register')
            self.io.button(False)
            return
        # game hold at page time T:  clock + (T - moved) - pressedAt
        T = s['moved'] + (s['pressedAt'] + hold + 0.5 - s['clock'])
        fire = T - self.offset - self.latency
        while time.perf_counter() * 1000 < fire - 2:
            time.sleep(0.0005)
        while time.perf_counter() * 1000 < fire:
            pass
        self.io.button(False)
        for _ in range(200):
            s2 = self.read()
            if s2 and (s2.get('turn') != 'held'):
                break
        v = s2.get('inputs') if s2 else None
        if v is None:
            return
        got_hold, got_x = v // 1024, v % 1024
        err = got_hold - hold
        self.latency += 0.5 * err
        pts = target_x(got_hold, cross)
        d = abs(pts - got_x)
        log(f'L{level} shot {len(s2["turns"]) or 6}: hold {got_hold} (plan {hold}, err {err:+d} ms), '
            f'x {got_x} vs target {pts} -> {"BULLSEYE" if d < 8 else f"off by {d}px"}'
            f'  [latency {self.latency:.1f} ms]')

    # --- game loop -------------------------------------------------------------
    def play_game(self):
        """From the Bullseye title screen to the submitted result. Returns score."""
        t_state = time.time()
        last = None
        while True:
            st = self.read()
            if st is None:
                return None
            key = (st['kind'], st.get('turn'), st['panel'], st.get('over'))
            if key != last:
                last, t_state = key, time.time()
            if time.time() - t_state > 30:
                log('stuck in', key)
                return None
            if st['kind'] == 'title':
                time.sleep(0.5)
                self.io.click_at(*PLAY_BTN)
                time.sleep(1.5)
            elif st['kind'] == 'playing':
                if st['over'] and st['panel']:
                    score = sum(st['scores'])
                    log(f'game over: levels {st["scores"]} -> {score}')
                    time.sleep(1.5)
                    fio.capture()  # (keeps the page rendering)
                    self.click_results_ok()
                    time.sleep(10)                   # let submitRun reach the server
                    return score
                if st['turn'] == 'waiting' and st['panel']:
                    time.sleep(1.2)                  # panel fade-in
                    self.io.click_at(*START_BTN)
                    time.sleep(0.6)
                elif st['turn'] == 'ready':
                    if self.sx is None:
                        self.calibrate_x()
                    self.shoot(st)
                else:
                    time.sleep(0.02)
            else:
                time.sleep(0.2)

    def click_results_ok(self):
        from PIL import Image
        path = os.path.join(HERE, 'results.png')
        Image.fromarray(fio.capture()).save(path)
        self.io.click_at(*RESULTS_OK)
        time.sleep(1.0)
        st = self.read()
        if st and st['panel']:
            log('results panel still open; see', path)


EXIT_BTN = (791, 72)          # Bullseye HUD exit door
HIGH_SCORES_BTN = (410, 338)  # on the "PLAY?" dialog
WALK_SPOTS = [(300, 420), (520, 400), (420, 360), (250, 380), (600, 420)]


def snap(name):
    from PIL import Image
    path = os.path.join(HERE, name)
    Image.fromarray(fio.capture()).save(path)
    return path


def leave_and_wander(io, bot, seconds=60):
    """Quit the minigame properly, then walk around the room for a while."""
    log('after-game screen saved to', snap('after_game.png'))
    for _ in range(5):
        st = bot.read()
        if st is None:
            break
        io.click_at(*EXIT_BTN)          # exit door (works on title + playing screens)
        time.sleep(3)
    log('left the game; screen:', stairies_bot.detect(fio.capture()))
    t0, i = time.time(), 0
    while time.time() - t0 < seconds:
        io.click_at(*WALK_SPOTS[i % len(WALK_SPOTS)])
        i += 1
        time.sleep(6)
    log(f'walked around for {seconds} s')


def check_high_scores(io, bot, tries=40):
    """Open the Bullseye dialog -> HIGH SCORES and save a screenshot."""
    for _ in range(tries):
        img = fio.capture()
        s = stairies_bot.detect(img)
        if s == 'lobby':
            io.click_at(*BULLSEYE_SIGN)
            time.sleep(1.5)
        elif s == 'dialog' or stairies_bot.matches(img, 'dialog'):
            io.click_at(*HIGH_SCORES_BTN)
            time.sleep(3)
            path = snap('high_scores.png')
            log('high score list saved to', path)
            return path
        else:
            time.sleep(1)
    log('could not open the high score list')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--games', type=int, default=3, help='max attempts until a 600 is submitted')
    ap.add_argument('--check', action='store_true', help='only open and screenshot the high score list')
    ap.add_argument('--wander', type=int, default=60, help='seconds to walk around after the game')
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = headless.launch(pw)
        install_patch(ctx)
        page.goto(headless.GAME_URL, wait_until='domcontentloaded')
        page.wait_for_timeout(8000)
        if '/game' not in page.url:
            log('logged out; logging in with .env credentials')
            if not headless.auto_login(page):
                raise SystemExit('login failed')
        io = headless.HeadlessIO(page, headless.GAME_ORIGIN)
        io.click_at = lambda gx, gy: (io.move(gx, gy), time.sleep(0.05), io.button(True),
                                       time.sleep(0.05), io.button(False))
        fio.set_backend(io)
        nav = stairies_bot.Bot()            # room navigation only
        nav.max_games, nav.reload, nav.unknown_strikes = None, None, 0
        bot = BullseyeBot(page, io)
        games = 0
        if a.check:
            games = a.games                 # skip playing
            while stairies_bot.detect(fio.capture()) != 'lobby':
                nav.step() if stairies_bot.detect(fio.capture()) != 'unknown' else time.sleep(1)
            check_high_scores(io, bot)
        last_s, since = None, time.time()
        try:
            while games < a.games:
                st = bot.read()
                if st is not None:
                    games += 1
                    score = bot.play_game()
                    log(f'attempt {games}: score {score}')
                    if score is not None:
                        leave_and_wander(io, bot, a.wander)
                    if score == 600:
                        log('600 submitted!')
                        check_high_scores(io, bot)
                        break
                    continue
                img = fio.capture()
                s = stairies_bot.detect(img)
                if s != last_s:
                    log('screen:', s)
                    last_s, since = s, time.time()
                elif time.time() - since > 20:
                    from PIL import Image
                    Image.fromarray(img).save(os.path.join(HERE, f'stuck_{s}.png'))
                    log(f'stuck on {s} for 20 s, saved stuck_{s}.png')
                    since = time.time()
                    if s == 'lobby':        # sign click missed: try the orb above it
                        io.click_at(618, 172)
                        time.sleep(1.5)
                if s == 'lobby':
                    io.click_at(*BULLSEYE_SIGN)
                    time.sleep(1.5)
                elif s == 'dialog' or stairies_bot.matches(img, 'dialog'):
                    io.click_at(*stairies_bot.CLICK['yes'])
                    time.sleep(3)
                elif s in ('server', 'map', 'room', 'grotto_outside', 'popup'):
                    nav.step()
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            io.button(False)
            ctx.close()


if __name__ == '__main__':
    main()
