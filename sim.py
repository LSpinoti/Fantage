"""Offline Stairies simulator, built from rules measured in logs/moves.csv
(analyze.py). Used to compare move-selection policies over many games.

    python3 sim.py [games_per_policy]

Model:
  * refill: uniform random colours; each new star is super with P_SUPER
  * move time: T_BASE + T_STEP * chain_steps seconds
  * locks spawn as a Poisson process, rate LOCK_RATE(level) per second,
    on random unlocked cells; every level-up removes all locks
  * level target: TARGET(level) move-points; level-up pays LEVEL_BONUS
  * game over when no legal (matching) move exists
"""
import math
import multiprocessing as mp
import random
import statistics as st
import sys

import solver

P_SUPER = 0.03
import os
T_BASE, T_STEP = float(os.environ.get("T_BASE", 0.85)), 0.22
LEVEL_BONUS = 1075


def LOCK_RATE(level):
    return max(0.0, 0.036 * (level - 3))


def TARGET(level):
    return 170 + 75 * level


def random_board(rng):
    while True:
        g = [[(rng.choice(solver.COLOURS), False, False) for _ in range(6)] for _ in range(6)]
        if not solver.has_match(g):
            return g


def play(policy, seed, max_moves=3000):
    rng = random.Random(seed)
    g = random_board(rng)
    level, lvl_pts, score, t, moves = 1, 0, 0, 0.0, 0
    srng = random.Random(seed + 1)
    while moves < max_moves:
        move = policy(g, TARGET(level) - lvl_pts, srng)
        if move is None:
            break
        g2 = solver.apply_move(g, move)
        if not solver.has_match(g2):
            break                       # policy returned an illegal move
        # resolve with random refills; new stars may be super
        res = solver.resolve(g2, rng)
        g = res.grid
        for r in range(6):
            for c in range(6):
                col, lk, sp = g[r][c]
                if not sp and not lk and rng.random() < P_SUPER * (res.cleared / 36):
                    g[r][c] = (col, lk, True)
        moves += 1
        dt = T_BASE + T_STEP * len(res.chains)
        t += dt
        score += res.points
        lvl_pts += res.points
        if lvl_pts >= TARGET(level):
            score += LEVEL_BONUS
            level += 1
            lvl_pts = 0
            g = [[(col, False, sp) for col, lk, sp in row] for row in g]
            continue
        # lock spawns during this move
        k = 0
        lam = LOCK_RATE(level) * dt
        # Poisson sample
        L, p = math.exp(-lam), 1.0
        while True:
            p *= rng.random()
            if p <= L:
                break
            k += 1
        free = [(r, c) for r in range(6) for c in range(6) if not g[r][c][1]]
        for r, c in rng.sample(free, min(k, len(free))):
            col, _, sp = g[r][c]
            g[r][c] = (col, True, sp)
    return score, level, moves


# ---------------------------------------------------------------- policies
def greedy(g, remaining, rng):
    best, bv = None, -1
    for m in solver.legal_lines(g):
        r = solver.simulate(g, m)
        if r is not None and r.points > bv:
            best, bv = m, r.points
    return best


def make_solver_policy(**weights):
    def pol(g, remaining, rng):
        old = {k: getattr(solver, k) for k in weights}
        for k, v in weights.items():
            setattr(solver, k, v)
        try:
            return solver.best_move(g, remaining=remaining, rng=rng)[0]
        finally:
            for k, v in old.items():
                setattr(solver, k, v)
    return pol


POLICIES = {
    'greedy': greedy,
    'solver': make_solver_policy(),
    'solver_noLevel': make_solver_policy(LEVEL_BONUS=0.0),
    'solver_freeLines12': make_solver_policy(W_FREE_LINE=12.0),
    'solver_freeLines2': make_solver_policy(W_FREE_LINE=2.0),
    'solver_next0.3': make_solver_policy(W_NEXT=0.3),
    'solver_next1.0': make_solver_policy(W_NEXT=1.0),
    'next0.3_free12': make_solver_policy(W_NEXT=0.3, W_FREE_LINE=12.0),
    'next0.3_noLevel': make_solver_policy(W_NEXT=0.3, LEVEL_BONUS=0.0),
    'next0': make_solver_policy(W_NEXT=0.0),
    'next0.3_s12': make_solver_policy(W_NEXT=0.3, SAMPLES=12, TOP_MOVES=14),
}


def _run(args):
    name, seed = args
    return name, play(POLICIES[name], seed)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    names = sys.argv[2].split(',') if len(sys.argv) > 2 else list(POLICIES)
    jobs = [(name, 1000 + i) for name in names for i in range(n)]   # same seeds per policy
    with mp.Pool() as pool:
        out = pool.map(_run, jobs)
    res = {}
    for name, r in out:
        res.setdefault(name, []).append(r)
    print(f'{"policy":22s} {"mean score":>10s} {"±se":>6s} {"median":>8s} {"mean lvl":>8s} {"moves":>6s}')
    for name in names:
        sc = [r[0] for r in res[name]]
        se = st.stdev(sc) / len(sc) ** 0.5 if len(sc) > 1 else 0
        print(f'{name:22s} {st.mean(sc):10.0f} {se:6.0f} {st.median(sc):8.0f} {st.mean(r[1] for r in res[name]):8.1f} '
              f'{st.mean(r[2] for r in res[name]):6.0f}')


if __name__ == '__main__':
    main()
