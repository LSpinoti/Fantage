"""Summarise logs/games.csv per experiment tag: coins/min and target gems/min.

    python3 games_report.py [target gem names, comma separated]
"""
import collections
import csv
import statistics as st
import sys

TARGETS = set((sys.argv[1] if len(sys.argv) > 1 else
               'Otherworldly Moonstone,Mythical Black Diamond').split(','))

SHORT = {'Rare Sapphire': 'Sapphire', 'Very Rare Ruby': 'Ruby', 'Ultra Rare Emerald': 'Emerald',
         'Legendary Diamond': 'Diamond', 'Mythical Black Diamond': 'BlackDiamond',
         'Otherworldly Moonstone': 'Moonstone'}

rows = list(csv.DictReader(open('logs/games.csv')))
by = collections.defaultdict(list)
for r in rows:
    by[r['tag']].append(r)

print(f'targets: {", ".join(sorted(TARGETS))}\n')
print(f'{"tag":15s} {"games":>5s} {"coins/game":>10s} {"cycle s":>8s} {"coins/min":>9s} '
      f'{"targets":>8s} {"target %":>8s} {"targets/hr":>10s}  gem mix')
for tag, rs in by.items():
    cyc = [float(r['cycle_s']) for r in rs if r['cycle_s']]
    mean_cycle = st.mean(cyc) if cyc else float('nan')
    coins = [int(r['stars_est']) for r in rs]
    gems_ = [r['gem'] for r in rs if r['gem']]
    tgt = sum(g in TARGETS for g in gems_)
    minutes = len(rs) * mean_cycle / 60          # every game costs one average cycle
    mix = collections.Counter(gems_).most_common()
    print(f'{tag:15s} {len(rs):5d} {st.mean(coins):10.0f} {mean_cycle:8.0f} {sum(coins) / minutes:9.0f} '
          f'{tgt:8d} {100 * tgt / max(1, len(gems_)):7.0f}% {60 * tgt / minutes:10.1f}  '
          + ', '.join(f'{SHORT.get(g, g)} {n}' for g, n in mix))
