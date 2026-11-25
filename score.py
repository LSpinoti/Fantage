"""Read the in-game "Score:" number by template-matching its digits.

Digits are yellow with a black outline. Each digit is a separate yellow blob:
we segment blobs left-to-right, normalise each to a fixed size and match it
against refs/digits/<d>.png (nearest by pixel difference).
"""
import os

import numpy as np
from scipy import ndimage
from PIL import Image

import fio

HERE = os.path.dirname(os.path.abspath(__file__))
DIGIT_DIR = os.path.join(HERE, 'refs', 'digits')
SCORE_BOX = (622, 32, 780, 60)     # game coords containing the score digits
NORM = (14, 22)                    # (w, h) each digit is resized to
MAX_DIST = 0.25                    # mean abs difference allowed (0..1)
SPLIT_W = 32                       # native px; wider blobs are two touching digits


def yellow_mask(img):
    s = fio.SCALE
    x0, y0, x1, y1 = SCORE_BOX
    p = img[y0 * s:y1 * s, x0 * s:x1 * s].astype(np.int16)
    return (p[..., 0] > 190) & (p[..., 1] > 170) & (p[..., 2] < 130)


def segments(mask, min_px=25):
    """Split the mask into digit blobs (connected components, left to right).
    Returns list of normalised float arrays (h, w) in [0, 1]."""
    lab, n = ndimage.label(mask)
    blobs = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        blob = lab[sl] == i
        if blob.sum() < min_px:
            continue
        x0 = sl[1].start
        # Touching digits form one wide blob: split at the thinnest column.
        parts = [(x0, blob)]
        while any(p.shape[1] > SPLIT_W for _, p in parts):
            nxt = []
            for px0, p in parts:
                if p.shape[1] > SPLIT_W:
                    cols = p.sum(0)
                    cut = 12 + int(np.argmin(cols[12:p.shape[1] - 12]))
                    nxt += [(px0, p[:, :cut]), (px0 + cut, p[:, cut:])]
                else:
                    nxt.append((px0, p))
            parts = nxt
        for px0, p in parts:
            rows = np.where(p.any(1))[0]
            cols_ = np.where(p.any(0))[0]
            if len(rows) and p.sum() >= min_px:
                blobs.append((px0 + cols_[0], p[rows[0]:rows[-1] + 1, cols_[0]:cols_[-1] + 1]))
    out = []
    for _, blob in sorted(blobs, key=lambda b: b[0]):
        im = Image.fromarray((blob * 255).astype(np.uint8)).resize(NORM, Image.BILINEAR)
        out.append(np.asarray(im, np.float32) / 255.0)
    return out


_templates = None


def _load():
    global _templates
    _templates = {}
    if os.path.isdir(DIGIT_DIR):
        for fn in os.listdir(DIGIT_DIR):
            if fn.endswith('.png') and fn[0].isdigit():
                _templates.setdefault(fn[0], []).append(
                    np.asarray(Image.open(os.path.join(DIGIT_DIR, fn)), np.float32) / 255.0)
    return _templates


def read_score(img):
    """Return the score as int, or None if it can't be read confidently."""
    t = _templates if _templates is not None else _load()
    segs = segments(yellow_mask(img))
    if not segs or not t:
        return None
    digits = []
    for seg in segs:
        best, bd = None, 1.0
        for d, refs in t.items():
            for r in refs:
                dist = float(np.abs(seg - r).mean())
                if dist < bd:
                    best, bd = d, dist
        if best is None or bd > MAX_DIST:
            return None
        digits.append(best)
    return int(''.join(digits))
