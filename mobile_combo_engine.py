from itertools import combinations
from math import exp, factorial, isfinite, prod
from functools import lru_cache

OUTCOMES = ('1', 'X', '2', '1X', '12', 'X2')
SIDES = ('Over 1.5', 'Under 2.5', 'Over 2.5', 'Under 3.5', 'Over 3.5', 'Goal', 'No Goal')

def number(value):
    try:
        n = float(str(value).replace(',', '.'))
        return n if isfinite(n) and n > 1 else None
    except (TypeError, ValueError): return None

def holds(market, h, a):
    if market in OUTCOMES:
        return {'1': h>a, 'X': h==a, '2': h<a, '1X': h>=a, '12': h!=a, 'X2': h<=a}[market]
    if market == 'Goal': return h>0 and a>0
    if market == 'No Goal': return h==0 or a==0
    kind, line = market.split(' ')
    return h+a > float(line) if kind == 'Over' else h+a < float(line)

@lru_cache(maxsize=1)
def grid():
    result = []
    for hi in range(3, 36, 2):
        for ai in range(3, 36, 2):
            lh, la = hi/10, ai/10
            cells = [(h,a,exp(-lh-la)*lh**h*la**a/factorial(h)/factorial(a)) for h in range(12) for a in range(12)]
            total = sum(p for h,a,p in cells)
            cells = [(h,a,p/total) for h,a,p in cells]
            probs = {m:sum(p for h,a,p in cells if holds(m,h,a)) for m in ('1','X','2','Goal','Over 1.5','Over 2.5','Over 3.5')}
            result.append((cells, probs))
    return result

def candidates(rows):
    result, seen = [], set()
    for row in rows:
        identity = (str(row.get('Data','')), str(row.get('Ora','')), str(row.get('Partita','')).strip())
        if not identity[2] or identity in seen: continue
        seen.add(identity)
        targets = {}
        for family in [('1','X','2'), ('Goal','No Goal')] + [(f'Over {line}',f'Under {line}') for line in ('1.5','2.5','3.5')]:
            odds = [number(row.get(m)) for m in family]
            if all(odds):
                total = sum(1/o for o in odds)
                targets.update({m:1/o/total for m,o in zip(family,odds) if m in ('1','X','2','Goal','Over 1.5','Over 2.5','Over 3.5')})
        if not all(m in targets for m in ('1','X','2')) or not any(m.startswith('Over') or m=='Goal' for m in targets): continue
        cells, _ = min(grid(), key=lambda item:sum((item[1][m]-p)**2 for m,p in targets.items()))
        combos = []
        for outcome in OUTCOMES:
            for side in SIDES:
                market = f'{outcome} + {side}'
                probability = sum(p for h,a,p in cells if holds(outcome,h,a) and holds(side,h,a))
                odd = number(row.get(market))
                if odd is not None and odd <= 1.20: continue
                if probability > 0:
                    combos.append({'partita':identity[2], 'data':identity[0], 'ora':identity[1], 'mercato':market,'prob':probability,'quota':odd,'event_id':identity})
        if combos:
            quoted = [c for c in combos if c['quota'] is not None]
            result.append(max(quoted or combos, key=lambda c:c['prob']))
    return sorted(result, key=lambda c:(-c['prob'],c['event_id']))[:30]

def best_ticket(events, target, max_events):
    valid = [e for e in events if number(e.get('quota')) and e['quota'] > 1.20]
    best = None
    for size in range(2,max_events+1):
        for legs in combinations(valid,size):
            if len({e['event_id'] for e in legs}) != size: continue
            odd = prod(e['quota'] for e in legs)
            if not target*.95 <= odd <= target*1.05: continue
            probability = prod(e['prob'] for e in legs)
            rank = (probability,-abs(odd-target),-size)
            if best is None or rank > best['rank']:
                best = {'legs':legs,'quota':odd,'prob':probability,'rank':rank}
    return best
