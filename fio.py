"""Low-level screen capture + mouse control for KDE Plasma (Wayland).

Capture: KWin org.kde.KWin.ScreenShot2.CaptureArea (raw RGBX, ~8 ms).
Input:   a uinput absolute pointer, which KWin maps 1:1 onto the logical
         workspace coordinate space (all monitors combined).

All coordinates in this module are *game coordinates*: logical pixels relative
to the top-left of the game's capture region (GAME_X, GAME_Y). Captured images
are at native resolution, i.e. SCALE x larger than game coordinates.
"""
import os
import time

import numpy as np

# Linux/KDE-only modules (on-screen mode). Imported lazily so the headless
# mode also runs on macOS/Windows, where these don't exist.
dbus = None


def _linux_input():
    from evdev import AbsInfo, UInput, ecodes
    return AbsInfo, UInput, ecodes

# Workspace bounding box (logical px) – eDP-1 0,132 1920x1200 ; DP-5 1920,0 2560x1440
WS_W, WS_H = 4480, 1440

# Game capture region in workspace logical coordinates.
GAME_X, GAME_Y, GAME_W, GAME_H = 540, 520, 840, 560
SCALE = 2  # eDP-1 scale factor (image px per logical px)

_shot = None
_backend = None   # None = on-screen (KWin + uinput); else e.g. headless.HeadlessIO


def set_backend(backend):
    """Route capture() and Mouse through another backend (headless browser)."""
    global _backend
    _backend = backend


def capture(x=0, y=0, w=GAME_W, h=GAME_H):
    """Capture a game-relative rect. Returns HxWx3 uint8 RGB at native res."""
    if _backend is not None:
        return _backend.capture(x, y, w, h)
    global _shot, dbus
    if _shot is None:
        import dbus as _dbus
        dbus = _dbus
        _shot = dbus.Interface(dbus.SessionBus().get_object('org.kde.KWin', '/org/kde/KWin/ScreenShot2'),
                               'org.kde.KWin.ScreenShot2')
    r, wfd = os.pipe()
    try:
        res = _shot.CaptureArea(GAME_X + x, GAME_Y + y, w, h,
                                {'native-resolution': True}, dbus.types.UnixFd(wfd))
    finally:
        os.close(wfd)
    W, H, S = int(res['width']), int(res['height']), int(res['stride'])
    buf = bytearray(S * H)
    mv = memoryview(buf)
    n = 0
    while n < len(buf):
        k = os.readv(r, [mv[n:]])
        if k == 0:
            break
        n += k
    os.close(r)
    # format 16 == QImage::Format_RGBX8888 -> bytes R,G,B,X
    return np.frombuffer(buf, np.uint8).reshape(H, S)[:, :W * 4].reshape(H, W, 4)[:, :, :3]


# Region needed while playing: gem icon + board (+ super-star ring margin).
PLAY_REGION = (40, 30, 740, 420)


def capture_play():
    """Capture only PLAY_REGION, returned inside a full-size frame (zeros
    elsewhere) so game coordinates stay valid. Faster in headless mode."""
    x, y, w, h = PLAY_REGION
    part = capture(x, y, w, h)
    full = np.zeros((GAME_H * SCALE, GAME_W * SCALE, 3), np.uint8)
    full[y * SCALE:y * SCALE + part.shape[0], x * SCALE:x * SCALE + part.shape[1]] = part
    return full


def capture_region_full(x, y, w, h):
    """Capture one small game-coords rect, placed in a full-size frame."""
    part = capture(x, y, w, h)
    full = np.zeros((GAME_H * SCALE, GAME_W * SCALE, 3), np.uint8)
    full[y * SCALE:y * SCALE + part.shape[0], x * SCALE:x * SCALE + part.shape[1]] = part
    return full


def px(img, gx, gy):
    """RGB of an image captured with capture() at game coords (gx, gy)."""
    return tuple(int(v) for v in img[int(gy * SCALE), int(gx * SCALE)])


def close(c1, c2, tol=30):
    return all(abs(a - b) <= tol for a, b in zip(c1, c2))


class Mouse:
    def __init__(self):
        self.ui = None
        self.backend = _backend
        if self.backend is not None:
            return
        AbsInfo, UInput, e = _linux_input()
        self._e = e
        cap = {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
               e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, WS_W - 1, 0, 0, 0)),
                          (e.ABS_Y, AbsInfo(0, 0, WS_H - 1, 0, 0, 0))]}
        self.ui = UInput(cap, name='staries-bot-pointer')
        time.sleep(0.8)  # let the compositor pick up the new device

    def move(self, gx, gy):
        if self.backend is not None:
            return self.backend.move(gx, gy)
        e = self._e
        self.ui.write(e.EV_ABS, e.ABS_X, int(round(GAME_X + gx)))
        self.ui.write(e.EV_ABS, e.ABS_Y, int(round(GAME_Y + gy)))
        self.ui.syn()

    def button(self, down):
        if self.backend is not None:
            return self.backend.button(down)
        e = self._e
        self.ui.write(e.EV_KEY, e.BTN_LEFT, 1 if down else 0)
        self.ui.syn()

    def click(self, gx, gy, hold=0.05):
        self.move(gx, gy)
        time.sleep(0.05)
        self.button(True)
        time.sleep(hold)
        self.button(False)
        time.sleep(0.05)

    def drag(self, x1, y1, x2, y2, steps=8, step_delay=0.012, hold=0.04):
        self.move(x1, y1)
        time.sleep(0.01)
        self.button(True)
        time.sleep(hold)
        for i in range(1, steps + 1):
            t = i / steps
            self.move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(step_delay)
        time.sleep(hold)
        self.button(False)
        time.sleep(0.01)

    def close(self):
        if self.ui is not None:
            self.ui.close()
