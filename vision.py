"""Pixel-based board reading for Stairies (game coordinates, see fio.py)."""
import os

import numpy as np

import fio

# Board geometry (game coords): centre of cell (r, c)
CELL0_X, CELL0_Y, PITCH = 437, 119, 60
N = 6
HALF = 22          # half-size of the box (game px) examined per cell

# Exact flat star colours (the bright centre of each star).
PALETTE = {
    'W': (193, 193, 193),  # white / silver
    'C': (52, 213, 245),   # cyan
    'R': (240, 66, 40),    # red
    'B': (46, 90, 244),    # blue
    'G': (9, 208, 39),     # green
    'P': (119, 56, 166),   # purple
    'M': (239, 50, 201),   # magenta
    'Y': (242, 211, 17),   # yellow
}
LOCK_RGB = np.array((47, 47, 61))
_KEYS = list(PALETTE)
_VALS = np.array([PALETTE[k] for k in _KEYS], dtype=np.int32)


def cell_xy(r, c):
    return CELL0_X + PITCH * c, CELL0_Y + PITCH * r


def _cell_patch(img, r, c, half, step=2):
    s = fio.SCALE
    gx, gy = cell_xy(r, c)
    return img[(gy - half) * s:(gy + half) * s:step, (gx - half) * s:(gx + half) * s:step]


def read_cell(img, r, c):
    """Return (colour_letter or '?', locked: bool)."""
    centre = _cell_patch(img, r, c, 3, 1).reshape(-1, 3).astype(np.int32)
    med = np.median(centre, 0)
    locked = np.abs(med - LOCK_RGB).sum() < 30
    if not locked:
        d = np.abs(_VALS - med).sum(1)
        k = int(d.argmin())
        if d[k] < 45:
            return _KEYS[k], False
    # Locked (or centre obscured): vote over pixels in the cell box that sit
    # very close to one of the flat palette colours.
    box = _cell_patch(img, r, c, HALF).reshape(-1, 3).astype(np.int32)
    d = np.abs(box[:, None, :] - _VALS[None, :, :]).sum(2)  # P x 8
    near = d.min(1) < 40
    votes = np.bincount(d.argmin(1)[near], minlength=len(_KEYS))
    k = int(votes.argmax())
    if votes[k] >= 12 and votes[k] >= 2 * np.partition(votes, -2)[-2]:
        return _KEYS[k], locked
    return '?', locked


def read_board(img=None):
    """Return (rows, locks): rows = 6 strings ('?' = unknown),
    locks = 6x6 list of bools."""
    if img is None:
        img = fio.capture()
    rows, locks = [], []
    for r in range(N):
        line, lk = [], []
        for c in range(N):
            col, l = read_cell(img, r, c)
            line.append(col)
            lk.append(l)
        rows.append(''.join(line))
        locks.append(lk)
    return rows, locks


GAP_RGB = np.array((38, 51, 72))   # dark navy board background between stars


def board_visible(img, need=18):
    """True if the dark board background shows at the gaps between stars."""
    s = fio.SCALE
    hits = 0
    for r in range(N - 1):
        for c in range(N - 1):
            gx, gy = cell_xy(r, c)
            gx += PITCH // 2
            gy += PITCH // 2
            p = img[(gy - 2) * s:(gy + 2) * s, (gx - 2) * s:(gx + 2) * s].reshape(-1, 3)
            if np.abs(np.median(p, 0) - GAP_RGB).sum() < 40:
                hits += 1
    return hits >= need


# Pixels of a 58x58 (half-res) cell box that are board background, i.e. not
# covered by a normal star (learned from normal boards).
_BG_MASK = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'refs', 'cell_bg_mask.npy'))
SUPER_THRESHOLD = 15


def fire_scores(img, half=29):
    """6x6 array: count of fiery-orange pixels in the background area around
    each star (the super-star ring). Normal boards score ~0."""
    s = fio.SCALE
    out = np.zeros((N, N), dtype=int)
    for r in range(N):
        for c in range(N):
            gx, gy = cell_xy(r, c)
            box = img[(gy - half) * s:(gy + half) * s:2, (gx - half) * s:(gx + half) * s:2].astype(np.int16)
            R, G, B = box[..., 0], box[..., 1], box[..., 2]
            orange = (R > 200) & (G > 60) & (G < 190) & (B < 100) & (R - G > 50)
            out[r, c] = int((orange & _BG_MASK).sum())
    return out


def read_supers(img):
    return (fire_scores(img) >= SUPER_THRESHOLD).tolist()


# Gem indicator (top-left of the game): black until a gem is earned, then purple.
GEM_BOX = (50, 45, 115, 90)   # game coords x0, y0, x1, y1


def gem_earned(img):
    s = fio.SCALE
    x0, y0, x1, y1 = GEM_BOX
    p = img[y0 * s:y1 * s:2, x0 * s:x1 * s:2].reshape(-1, 3).astype(np.int16)
    R, G, B = p[:, 0], p[:, 1], p[:, 2]
    purple = ((B > 150) & (R > 70) & (G < 110) & (B > G + 60)).mean()
    return purple > 0.4


# Level-progress star meter (left side): green fill rising from the bottom.
METER_COLS = (200, 250)     # game x range sampled
METER_ROWS = (125, 262)     # game y range searched


def meter_top(img):
    """Game-y of the top edge of the green meter fill (smaller = fuller),
    or None if no fill is visible."""
    s = fio.SCALE
    x0, x1 = METER_COLS
    y0, y1 = METER_ROWS
    p = img[y0 * s:y1 * s, x0 * s:x1 * s].astype(np.int16)
    R, G, B = p[..., 0], p[..., 1], p[..., 2]
    green = (G > R + 50) & (G > B + 10) & (G > 120)
    tops = [np.argmax(col) for col in green.T if col.any()]
    if len(tops) < (x1 - x0):          # need green in at least half the columns
        return None
    return y0 + float(np.median(tops)) / s


def board_complete(rows):
    return all('?' not in row for row in rows)


def fmt(rows, locks):
    return ' '.join(''.join(ch.lower() if locks[r][c] else ch for c, ch in enumerate(row))
                    for r, row in enumerate(rows))
