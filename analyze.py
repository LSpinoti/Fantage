"""Analyse logs/moves.csv: scoring rules, chain multipliers, super stars,
level targets and the meter -> progress mapping.

    python3 analyze.py [logs/moves.csv ...]
"""
import collections
import csv
import statistics as st
import sys


def load(paths):
    rows = []
    for p in paths:
        with open(p) as f:
            for r in csv.DictReader(f):
                if 'pred_chains' not in r:
                    continue
                for k in ('game', 'level', 'move', 'score_before', 'points', 'score_after', 'pred_points',
                          'pred_blasted', 'pred_supers', 'locks', 'supers'):
                    r[k] = int(r[k]) if r.get(k) not in (None, '') else None
                r['meter_top'] = float(r['meter_top']) if r.get('meter_top') else None
                r['chains'] = [list(map(int, step.split('+'))) for step in r['pred_chains'].split('/')] \
                    if r['pred_chains'] else []
                rows.append(r)
    return rows


def main(paths):
    R = load(paths)
    moves = [r for r in R if r['points'] is not None and r['points'] < 500 and r['chains']]
    print(f'{len(R)} rows, {len(moves)} usable moves\n')

    # 1. single-step, single-group moves without supers: base points by size
    print('== single group, one chain step predicted, no super ==')
    by = collections.defaultdict(list)
    for r in moves:
        if len(r['chains']) == 1 and len(r['chains'][0]) == 1 and not r['pred_supers']:
            by[r['chains'][0][0]].append(r['points'])
    for size in sorted(by):
        c = collections.Counter(by[size])
        print(f'  size {size}: n={len(by[size])}  most common actual: {c.most_common(4)}  min={min(by[size])}')

    # 2. one step with two groups
    print('\n== one chain step, several groups ==')
    by = collections.defaultdict(list)
    for r in moves:
        if len(r['chains']) == 1 and len(r['chains'][0]) > 1 and not r['pred_supers']:
            by[tuple(sorted(r['chains'][0]))].append(r['points'])
    for k in sorted(by):
        print(f'  {k}: n={len(by[k])} most common: {collections.Counter(by[k]).most_common(3)} min={min(by[k])}')

    # 3. multi-step chains
    print('\n== predicted multi-step chains (actual can only be higher: unknown refills) ==')
    by = collections.defaultdict(list)
    for r in moves:
        if len(r['chains']) > 1 and not r['pred_supers']:
            by[r['pred_chains']].append(r['points'])
    for k in sorted(by, key=lambda k: (len(k), k))[:25]:
        print(f'  {k}: n={len(by[k])} min={min(by[k])} most common: {collections.Counter(by[k]).most_common(3)}')

    # 4. super stars
    print('\n== moves predicted to clear a super star ==')
    for r in moves:
        if r['pred_supers']:
            print(f"  chains={r['pred_chains']} blasted={r['pred_blasted']} pred={r['pred_points']} actual={r['points']}")

    # 5. accuracy of the current scoring model
    exact = sum(r['points'] == r['pred_points'] for r in moves)
    under = sum(r['points'] > r['pred_points'] for r in moves)
    print(f'\nmodel: exact {exact}/{len(moves)}, actual higher {under}, actual lower {len(moves) - exact - under}')

    # 6. levels: move points per level, and meter vs progress
    print('\n== levels ==')
    games = collections.defaultdict(list)
    for r in R:
        games[r['game']].append(r)
    for g, rs in games.items():
        lv = collections.defaultdict(list)
        for r in rs:
            lv[r['level']].append(r)
        for L in sorted(lv):
            seg = lv[L]
            pts = [r['points'] for r in seg if r['points'] is not None and r['points'] < 500]
            bonus = [r['points'] for r in seg if r['points'] is not None and r['points'] >= 500]
            print(f'  game {g} level {L:2d}: moves {len(seg):3d}  move-points {sum(pts):5d}  '
                  f'mean {st.mean(pts) if pts else 0:5.1f}  bonus {bonus}  max locks {max(r["locks"] for r in seg)}')

    # 7. meter_top against points gained so far in the level (completed levels only)
    print('\n== meter vs fraction of level done (completed levels) ==')
    pairs = []
    for g, rs in games.items():
        lv = collections.defaultdict(list)
        for r in rs:
            lv[r['level']].append(r)
        for L, seg in lv.items():
            if not any(r['points'] is not None and r['points'] >= 500 for r in seg):
                continue
            total = sum(r['points'] for r in seg if r['points'] is not None and r['points'] < 500)
            done = 0
            for r in seg:
                if r['meter_top'] is not None and total:
                    pairs.append((done / total, r['meter_top'], L))
                if r['points'] is not None and r['points'] < 500:
                    done += r['points']
    bins = collections.defaultdict(list)
    for frac, top, L in pairs:
        bins[round(frac * 10) / 10].append(top)
    for b in sorted(bins):
        print(f'  done≈{b:.1f}: meter_top median {st.median(bins[b]):6.1f}  (n={len(bins[b])})')


if __name__ == '__main__':
    main(sys.argv[1:] or ['logs/moves.csv'])
