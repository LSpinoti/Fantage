"""Stairies solver.

Rules (verified against the live game):
  * 6x6 board, 8 colours. Dragging a row/column slides the whole line
    cyclically by the number of cells dragged.
  * A move is accepted only if it creates at least one 4-connected
    same-colour group of >= 3 stars; otherwise the line snaps back.
  * Cleared stars are removed, stars above fall, new random stars drop in
    from the top, and new groups clear again (chains / combos).
  * Locked stars cannot move, so a row/column containing one cannot be
    dragged. A locked star still matches by colour and is cleared (lock and
    all) when part of a group. Locked stars fall with gravity.
  * Super stars (orange fire ring) blast the 3x3 box around them when cleared.
  * Points fill the level meter; each level-up pays a ~1000 bonus and wipes
    every lock. The game ends when no legal move exists.

Grid: list of 6 rows of 6 cells; cell = (colour, locked, super); colour '?' = unknown.
Move: ('r', i, k) = shift row i right by k ; ('c', i, k) = shift column i down by k.
"""
import random

N = 6
UNKNOWN = '?'
COLOURS = 'WCRBGPMY'
UNK_CELL = (UNKNOWN, False, False)

ALL_MOVES = [(t, i, k) for t in 'rc' for i in range(N) for k in range(1, N)]

# ---------------------------------------------------------------- scoring
# Scoring (fitted from logged games, analyze.py): each chain step scores
#   sum over its groups of 5*(size-1)   +   10 for every extra group in that step.
# No chain multiplier. Stars removed only by a super-star blast score nothing
# directly (their value is the extra cascades and cleared locks).
def step_points(sizes):
    return sum(5 * (n - 1) for n in sizes) + 10 * (len(sizes) - 1)


# ---------------------------------------------------------------- board ops
def make_grid(rows, locks, supers=None):
    return [[(rows[r][c], bool(locks[r][c]), bool(supers[r][c]) if supers else False)
             for c in range(N)] for r in range(N)]


def line_locked(g, t, i):
    if t == 'r':
        return any(g[i][c][1] for c in range(N))
    return any(g[r][i][1] for r in range(N))


def free_lines(g):
    return sum(not line_locked(g, t, i) for t in 'rc' for i in range(N))


def legal_lines(g):
    out = []
    for t in 'rc':
        for i in range(N):
            if not line_locked(g, t, i):
                out.extend((t, i, k) for k in range(1, N))
    return out


def apply_move(g, move):
    t, i, k = move
    g = [row[:] for row in g]
    if t == 'r':
        row = g[i]
        g[i] = row[-k:] + row[:-k]
    else:
        col = [g[r][i] for r in range(N)]
        col = col[-k:] + col[:-k]
        for r in range(N):
            g[r][i] = col[r]
    return g


def clusters(g):
    """All 4-connected same-colour groups of size >= 3 (unknowns excluded)."""
    seen = [[False] * N for _ in range(N)]
    out = []
    for r in range(N):
        for c in range(N):
            if seen[r][c]:
                continue
            seen[r][c] = True
            col = g[r][c][0]
            if col == UNKNOWN:
                continue
            stack = [(r, c)]
            comp = []
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < N and 0 <= nx < N and not seen[ny][nx] and g[ny][nx][0] == col:
                        seen[ny][nx] = True
                        stack.append((ny, nx))
            if len(comp) >= 3:
                out.append(comp)
    return out


def has_match(g):
    return bool(clusters(g))


class Result:
    __slots__ = ('points', 'cleared', 'locks', 'supers', 'blasted', 'chains', 'grid')

    def __init__(self):
        self.points = 0
        self.cleared = 0
        self.locks = 0
        self.supers = 0
        self.blasted = 0
        self.chains = []     # per chain step: list of group sizes


def resolve(g, rng=None):
    """Clear groups (+ super-star blasts), apply gravity, repeat while groups
    form. New stars are random colours if `rng` is given, else unknown.
    Mutates g; returns a Result."""
    res = Result()
    chain = 0
    while True:
        cl = clusters(g)
        if not cl:
            break
        chain += 1
        res.chains.append([len(c) for c in cl])
        res.points += step_points([len(c) for c in cl])
        gone = set()
        for comp in cl:
            gone.update(comp)
        # super-star blasts (a blasted super star blasts too)
        todo = [p for p in gone if g[p[0]][p[1]][2]]
        done = set()
        while todo:
            y, x = todo.pop()
            if (y, x) in done:
                continue
            done.add((y, x))
            for yy in range(max(0, y - 1), min(N, y + 2)):
                for xx in range(max(0, x - 1), min(N, x + 2)):
                    if (yy, xx) not in gone:
                        gone.add((yy, xx))
                        res.blasted += 1
                        if g[yy][xx][2]:
                            todo.append((yy, xx))
        res.supers += len(done)
        for y, x in gone:
            res.cleared += 1
            res.locks += g[y][x][1]
            g[y][x] = None
        for x in range(N):
            col = [g[y][x] for y in range(N) if g[y][x] is not None]
            missing = N - len(col)
            if rng is None:
                new = [UNK_CELL] * missing
            else:
                new = [(rng.choice(COLOURS), False, False) for _ in range(missing)]
            col = new + col
            for y in range(N):
                g[y][x] = col[y]
    res.grid = g
    return res


def simulate(g, move, rng=None):
    """Apply a move and resolve it. Returns Result, or None if illegal."""
    g2 = apply_move(g, move)
    if not has_match(g2):
        return None
    return resolve(g2, rng)


# ---------------------------------------------------------------- policy
# Weights (tuned by analyze.py / live runs)
SAMPLES = 6            # random refills sampled per candidate move
TOP_MOVES = 10         # candidates that get the sampled evaluation
W_NEXT = 0.6           # value of the best follow-up move's points
W_FREE_LINE = 6.0      # per row/column without a lock afterwards
W_DEAD = 400.0         # penalty when no legal follow-up move exists
LEVEL_BONUS = 1500.0   # value of finishing the level (≈1000 points + lock wipe)


def _best_immediate(g):
    """Points of the best single move on g (known stars only, no sampling)."""
    best = 0
    n = 0
    for m in legal_lines(g):
        g2 = apply_move(g, m)
        cl = clusters(g2)
        if not cl:
            continue
        n += 1
        p = step_points([len(c) for c in cl])
        if p > best:
            best = p
    return best, n


def best_move(g, exclude=(), remaining=None, rng=None):
    """Pick a move. `remaining` = estimated points still needed to level up
    (None if unknown). Returns (move, value) or (None, 0)."""
    rng = rng or random.Random(0)
    cands = []
    for m in legal_lines(g):
        if m in exclude:
            continue
        r = simulate(g, m)          # deterministic: known stars only
        if r is None:
            continue
        cands.append((r.points + 5 * r.locks + 3 * r.supers, m, r))
    if not cands:
        return None, 0.0
    cands.sort(key=lambda x: -x[0])
    best, best_v = None, float('-inf')
    for quick, m, r0 in cands[:TOP_MOVES]:
        tot = 0.0
        for _ in range(SAMPLES):
            r = simulate(g, m, rng)
            v = r.points
            if remaining is not None and r.points >= remaining:
                v += LEVEL_BONUS
            nxt, n_legal = _best_immediate(r.grid)
            v += W_NEXT * nxt
            v += W_FREE_LINE * free_lines(r.grid)
            if n_legal == 0:
                v -= W_DEAD
            tot += v
        v = tot / SAMPLES - 0.01 * min(m[2], N - m[2])
        if v > best_v:
            best, best_v = m, v
    return best, best_v


def drag_cells(move):
    """(start_cell, end_cell) as (row, col) pairs for a move, dragging the
    short way round (left/up when k > 3)."""
    t, i, k = move
    if k <= N // 2:
        a, d = 0, k
    else:
        a, d = N - 1, k - N
    if t == 'r':
        return (i, a), (i, a + d)
    return (a, i), (a + d, i)


if __name__ == '__main__':
    import time
    rows = ["GGMBBW", "WRWWRM", "PWPGPB", "CWRRCP", "YGBBRC", "YGRMYB"]
    locks = [[False] * N for _ in range(N)]
    for r, c in [(0, 1), (0, 5), (1, 3), (1, 5), (3, 1)]:
        locks[r][c] = True
    supers = [[False] * N for _ in range(N)]
    supers[2][2] = True
    g = make_grid(rows, locks, supers)
    t = time.time()
    print(best_move(g), '%.3fs' % (time.time() - t))
    t = time.time()
    print(best_move(g, remaining=20), '%.3fs' % (time.time() - t))
    rows = ["WCRBGP", "BBPCBR", "MMYCYG", "YCRRGW", "CGPGRC", "GBRCYW"]
    g = make_grid(rows, [[False] * N for _ in range(N)])
    t = time.time()
    print(best_move(g), '%.3fs' % (time.time() - t))
