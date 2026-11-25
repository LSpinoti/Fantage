"""Gem-type recognition on the "YOU HAVE FOUND A GEM!" dialog, plus stats.

The gem's name (yellow text under the icon) is turned into a binary pixel
mask and compared with known masks in refs/gems/<key>.png. Names live in
refs/gems/names.json. An unseen gem is stored as a new type "new_gem_N"
(its dialog crop saved next to it) so it can be renamed in names.json.
"""
import json
import os
import time

import numpy as np
from PIL import Image

import fio

HERE = os.path.dirname(os.path.abspath(__file__))
GEM_DIR = os.path.join(HERE, 'refs', 'gems')
NAMES = os.path.join(GEM_DIR, 'names.json')
STATS = os.path.join(HERE, 'logs', 'gem_stats.json')
HISTORY = os.path.join(HERE, 'logs', 'gems.log')

NAME_BOX = (300, 284, 540, 306)     # game coords of the yellow gem-name text
DIALOG_BOX = (260, 180, 580, 320)   # icon + name, saved for new types
MATCH_DIST = 0.25                   # Jaccard distance; same name ~0, different >> 0.3


def name_mask(img):
    s = fio.SCALE
    x0, y0, x1, y1 = NAME_BOX
    p = img[y0 * s:y1 * s, x0 * s:x1 * s].astype(np.int16)
    return (p[..., 0] > 190) & (p[..., 1] > 140) & (p[..., 2] < 90)


def _dist(a, b, shift=3):
    """Jaccard distance, minimised over small shifts (on-screen and headless
    rendering differ by a pixel or so)."""
    best = 1.0
    for dy in range(-shift, shift + 1):
        for dx in range(-shift, shift + 1):
            bb = np.roll(np.roll(b, dy, 0), dx, 1)
            union = (a | bb).sum()
            if union:
                best = min(best, float((a ^ bb).sum()) / union)
    return best


def _load_names():
    try:
        with open(NAMES) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def classify(img):
    """Return (key, display_name). Registers unseen gems as new types."""
    m = name_mask(img)
    if m.sum() < 30:
        return None, None
    names = _load_names()
    best, best_d = None, 1.0
    for fn in sorted(os.listdir(GEM_DIR)):
        if not fn.endswith('.png') or fn.endswith('_dialog.png'):
            continue
        ref = np.asarray(Image.open(os.path.join(GEM_DIR, fn))) > 0
        if ref.shape != m.shape:
            continue
        d = _dist(m, ref)
        if d < best_d:
            best, best_d = fn[:-4], d
    if best is not None and best_d < MATCH_DIST:
        return best, names.get(best, best)
    # new gem type
    n = 1
    while os.path.exists(os.path.join(GEM_DIR, f'new_gem_{n}.png')):
        n += 1
    key = f'new_gem_{n}'
    Image.fromarray((m * 255).astype(np.uint8)).save(os.path.join(GEM_DIR, key + '.png'))
    s = fio.SCALE
    x0, y0, x1, y1 = DIALOG_BOX
    Image.fromarray(img[y0 * s:y1 * s, x0 * s:x1 * s]).save(os.path.join(GEM_DIR, key + '_dialog.png'))
    names[key] = key
    with open(NAMES, 'w') as f:
        json.dump(names, f, indent=2)
    return key, key


def record(key):
    """Add one observation; returns {display name: count} for all gems seen."""
    os.makedirs(os.path.dirname(STATS), exist_ok=True)
    try:
        with open(STATS) as f:
            counts = json.load(f)
    except FileNotFoundError:
        counts = {}
    counts[key] = counts.get(key, 0) + 1
    with open(STATS, 'w') as f:
        json.dump(counts, f, indent=2)
    names = _load_names()
    with open(HISTORY, 'a') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{names.get(key, key)}\n")
    return {names.get(k, k): v for k, v in counts.items()}


def summary(counts):
    total = sum(counts.values())
    return ', '.join(f'{k}: {v} ({100 * v / total:.0f}%)'
                     for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) + f'  [total {total}]'


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'stats':
        names = _load_names()
        with open(STATS) as f:
            print(summary({names.get(k, k): v for k, v in json.load(f).items()}))
