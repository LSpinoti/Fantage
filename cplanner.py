"""ctypes wrapper for the C++ planner (cpp/staries.cpp).

The shared library is (re)built automatically with g++ when missing or older
than the source. plan() has the same meaning as planner.plan(), but is fast
enough for a deeper search.
"""
import ctypes
import os
import subprocess

import planner

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'cpp', 'staries.cpp')
LIB = os.path.join(HERE, 'cpp', 'libstaries.so')

DEPTH = 8
BEAM = 100

_lib = None


def _build():
    """Compile the planner for this machine (g++ on Linux, clang/c++ on macOS)."""
    import shutil
    cxx = shutil.which('g++') or shutil.which('c++') or shutil.which('clang++')
    if not cxx:
        raise RuntimeError('no C++ compiler found')
    base = [cxx, '-O3', '-std=c++17', '-shared', '-fPIC', '-o', LIB, SRC]
    for extra in (['-march=native'], ['-mcpu=native'], []):   # Apple Silicon may reject -march=native
        if subprocess.run(base[:2] + extra + base[2:], capture_output=True).returncode == 0:
            return
    raise RuntimeError('could not compile ' + SRC)


def _load():
    global _lib
    if _lib is not None:
        return _lib
    if not os.path.exists(LIB) or os.path.getmtime(LIB) < os.path.getmtime(SRC):
        _build()
    try:
        lib = ctypes.CDLL(LIB)
    except OSError:              # e.g. a library copied from another OS / CPU: rebuild it
        _build()
        lib = ctypes.CDLL(LIB)
    f = lib.staries_plan
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_uint32, ctypes.c_double,
                  ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.POINTER(ctypes.c_double),
                  ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double)]
    _lib = lib
    return lib


def available():
    try:
        _load()
        return True
    except Exception:
        return False


def plan(game, t0=None, depth=DEPTH, beam=BEAM):
    """game: stariesgame.Game in phase 'play'. Returns (move, value, info)."""
    lib = _load()
    flat = []
    for x in range(6):
        for y in range(6):
            c, lk, bm = game.board[x][y]
            flat += [int(c), int(bool(lk)), int(bool(bm))]
    board = (ctypes.c_int * 108)(*flat)
    weights = (ctypes.c_double * 4)(planner.W_BROKEN, planner.W_FREE, planner.W_OVER, planner.W_DEPTH_DISCOUNT)
    out = (ctypes.c_int * 3)()
    val = ctypes.c_double()
    n = lib.staries_plan(board, game.level, game.score, game.broken, game.needed, float(game.now),
                         float(game.last_lock), float(game.lock_interval), game.rng.value & 0xFFFFFFFF,
                         float(game.now if t0 is None else t0), depth, beam, float(planner.REACT_MS),
                         weights, out, ctypes.byref(val))
    if n == 0:
        return None, 0.0, {}
    return (out[0], out[1], out[2]), val.value, {'candidates': n}
