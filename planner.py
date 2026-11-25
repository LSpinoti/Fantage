"""Exact look-ahead planner for Staries, on top of stariesgame (exact port).

Because refills, bombs and locks come from the game's seeded RNG, every move
sequence can be simulated exactly. We beam-search sequences of DEPTH moves and
play the first move of the best one.

Each simulated move starts REACT_MS after the previous one finished (the bot's
reaction time), so lock timing is predicted too.
"""
import stariesgame as sg

DEPTH = 3
BEAM = 10
REACT_MS = 150          # bot latency between logic 'play' and our next input
W_BROKEN = 4.0          # value per star broken (level progress; a level-up = +1000)
W_FREE = 4.0            # per row/column without a lock at the end of the line
W_OVER = 5000.0         # game over
W_DEPTH_DISCOUNT = 0.9  # later moves are worth slightly less (plans change)


def free_lines(game):
    return sum(game.can_slide(a, i) for a in (0, 1) for i in range(sg.N))


def leaf_value(game):
    if game.phase == 'over':
        return -W_OVER
    return W_FREE * free_lines(game)


def plan(game, t0=None, depth=DEPTH, beam=BEAM):
    """Return (best_first_move, value, info) for a Game in phase 'play'.
    t0 = game time at which the first move will be made."""
    start = game.now if t0 is None else t0
    # each beam entry: (accumulated value, first move, game after line, next start time)
    frontier = [(0.0, None, game, start)]
    best_first = {}
    for d in range(depth):
        cands = []
        w = W_DEPTH_DISCOUNT ** d
        for acc, first, g, t in frontier:
            if g.phase != 'play':
                continue
            for mv in g.legal_moves():
                g2 = g.copy()
                st = g2.slide(mv, t=t)
                if st is None:
                    continue
                v = acc + w * (st['points'] + W_BROKEN * st['broken'])
                f = first or mv
                cands.append((v, f, g2, st['end'] + REACT_MS))
        if not cands:
            break
        # evaluate leaves for ranking; keep the best `beam`
        scored = [(v + leaf_value(g2), v, f, g2, t) for v, f, g2, t in cands]
        scored.sort(key=lambda x: -x[0])
        for total, v, f, g2, t in scored:
            if f not in best_first or total > best_first[f]:
                best_first[f] = total
        frontier = [(v, f, g2, t) for _, v, f, g2, t in scored[:beam]]
    if not best_first:
        return None, 0.0, {}
    mv = max(best_first, key=best_first.get)
    return mv, best_first[mv], {'candidates': len(best_first)}
