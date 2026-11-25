"""Headless browser backend: runs Fantage in its own GPU-accelerated
Chromium (Playwright) and drives it with DevTools input events, so the real
screen and mouse are never touched.

  .venv/bin/python headless.py login    # one-time: visible window, log in, close it
  .venv/bin/python headless.py shot     # save a screenshot of the headless page

The bot uses it via:  stairies_bot.py --headless
"""
import base64
import io
import os
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, '.browser-profile')
GAME_URL = 'https://playfantage.com/game'

VIEWPORT = {'width': 1920, 'height': 1080}
# Top-left of the game capture region in CSS px (matches the on-screen framing).
# The game frame is centred in the page: origin = (width/2 - 420, height/2 - 272.5).
GAME_ORIGIN = (540, 267.5)


def game_origin(page):
    w, h = page.evaluate('[innerWidth, innerHeight]')
    return w / 2 - 420, h / 2 - 272.5
DPR = 2  # same pixel density as the laptop panel, so reference images still match

# GPU rendering: on Linux force ANGLE over EGL (otherwise headless falls back to
# software rendering); on macOS/Windows Chromium's default backend is fine.
_ANGLE = ['--use-angle=gl-egl'] if sys.platform.startswith('linux') else []
GPU_ARGS = ['--enable-gpu', *_ANGLE, '--ignore-gpu-blocklist',
            # keep running at full speed while "hidden"
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding']


def launch(pw, headless=True):
    """Hidden browser: emulated 1920x1080 viewport at 2x. Visible browser
    (headless=False): a normal window using the screen's real scale, because
    emulated 2x screenshots force a re-render that makes a visible window flicker."""
    if headless:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, headless=True, args=GPU_ARGS,
            viewport=VIEWPORT, device_scale_factor=DPR)
    else:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, headless=False, args=GPU_ARGS + ['--window-size=1920,1200'], no_viewport=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


class HeadlessIO:
    """Capture + mouse with the same interface as fio's KWin/uinput backend.
    Coordinates are game coords: CSS px relative to (origin_x, origin_y)."""

    def __init__(self, page, origin=(0, 0), native=False):
        """native=True: a visible window at the screen's real 2x scale; screenshots
        are taken at scale 1 (already device pixels) and the origin follows the
        window size."""
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.native = native
        self.ox, self.oy = game_origin(page) if native else origin
        self._origin_at = time.time()
        self.x = self.y = 0.0
        self.pressed = False

    # --- capture -------------------------------------------------------
    def capture(self, x, y, w, h):
        # Chromium rounds fractional clip origins, so clip from whole CSS px
        # (one px larger) and drop the sub-pixel remainder in device pixels.
        if self.native and time.time() - self._origin_at > 2:
            try:                                   # window may have been resized
                self.ox, self.oy = game_origin(self.page)
            except Exception:
                pass
            self._origin_at = time.time()
        fx, fy = self.ox + x, self.oy + y
        ix, iy = int(fx), int(fy)
        dx, dy = int(round((fx - ix) * DPR)), int(round((fy - iy) * DPR))
        r = self.cdp.send('Page.captureScreenshot', {
            'format': 'png',
            'clip': {'x': ix, 'y': iy, 'width': w + 1, 'height': h + 1, 'scale': 1 if self.native else DPR},
            'captureBeyondViewport': False, 'optimizeForSpeed': True})
        a = np.asarray(Image.open(io.BytesIO(base64.b64decode(r['data']))).convert('RGB'))
        return a[dy:dy + h * DPR, dx:dx + w * DPR]

    # --- mouse ---------------------------------------------------------
    def _mouse(self, typ, button='none', clicks=0):
        self.cdp.send('Input.dispatchMouseEvent', {
            'type': typ, 'x': self.ox + self.x, 'y': self.oy + self.y,
            'button': button, 'buttons': 1 if self.pressed else 0, 'clickCount': clicks})

    def move(self, gx, gy):
        self.x, self.y = float(gx), float(gy)
        self._mouse('mouseMoved', 'left' if self.pressed else 'none')

    def button(self, down):
        self.pressed = bool(down)
        self._mouse('mousePressed' if down else 'mouseReleased', 'left', 1)


def load_credentials(path=os.path.join(HERE, '.env')):
    """Read EMAIL and PASSWORD from .env (KEY=VALUE lines, optional quotes)."""
    creds = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                creds[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    if creds.get('EMAIL') and creds.get('PASSWORD'):
        return creds['EMAIL'], creds['PASSWORD']
    return None


def auto_login(page):
    """Log in on the homepage form with the .env credentials. Returns True
    if the game page is reachable afterwards."""
    creds = load_credentials()
    if creds is None:
        return False
    email, password = creds
    page.goto('https://playfantage.com/', wait_until='domcontentloaded')
    page.wait_for_timeout(3000)   # let any pending redirect finish
    page.locator('input[name=email]').first.fill(email, timeout=15000)
    page.locator('input[name=password]').first.fill(password, timeout=5000)
    try:   # "remember me" is a styled checkbox; best effort
        page.locator('input[name=remember]').first.check(force=True, timeout=3000)
    except Exception:
        pass
    # The LOGIN button is animated (never "stable"), so submit with Enter.
    page.locator('input[name=password]').first.press('Enter')
    page.wait_for_timeout(3000)
    if page.locator('input[name=password]').count():
        try:
            page.locator('button[type=submit]', has_text='LOGIN').first.click(force=True, timeout=3000)
        except Exception:
            pass
    page.wait_for_timeout(5000)
    page.goto(GAME_URL, wait_until='domcontentloaded')
    page.wait_for_timeout(8000)
    return '/game' in page.url


def _login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = launch(pw, headless=False)
        page.goto('https://playfantage.com/', wait_until='domcontentloaded')
        print('Log in in the window that opened, get into the game once, then close the window.')
        try:
            page.wait_for_event('close', timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    print('Profile saved to', PROFILE)


def _shot():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = launch(pw)
        page.goto(GAME_URL, wait_until='domcontentloaded')
        page.wait_for_timeout(15000)
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'logs', 'headless.png')
        page.screenshot(path=out)
        print(page.url, '->', out)
        ctx.close()


if __name__ == '__main__':
    {'login': _login, 'shot': _shot}[sys.argv[1]]()
