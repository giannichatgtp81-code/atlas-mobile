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

@lru_cache(maxsize=2048)
def fitted_probabilities(target_items):
    targets = dict(target_items)
    cells, _ = min(grid(), key=lambda item:sum((item[1][m]-p)**2 for m,p in targets.items()))
    simple = {m:sum(p for h,a,p in cells if holds(m,h,a)) for m in OUTCOMES + SIDES + ('Under 1.5',)}
    combos = {f'{o} + {s}':sum(p for h,a,p in cells if holds(o,h,a) and holds(s,h,a)) for o in OUTCOMES for s in SIDES}
    return simple, combos

def analyse_palinsesto(rows):
    result, daily, seen = [], [], set()
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
        context = row.get('_pitchapi')
        if context:
            # Experimental conservative blend; quote prices are never modified.
            historical = context['targets']
            targets = {m: 0.75*p + 0.25*historical[m] for m,p in targets.items()}
        simple_probs, combo_probs = fitted_probabilities(tuple(sorted(targets.items())))
        combos = []
        event_options = []
        for market, probability in simple_probs.items():
            odd = number(row.get(market))
            if odd and odd > 1.20:
                event_options.append({'partita':identity[2], 'data':identity[0], 'ora':identity[1], 'mercato':market,'prob':probability,'quota':odd,'quota_stimata':False,'event_id':identity})
        for market, probability in combo_probs.items():
                real_odd = number(row.get(market))
                odd = real_odd if real_odd is not None else (1 / probability if probability > 0 else None)
                if odd is None or odd <= 1.20: continue
                if probability > 0:
                    pick = {'partita':identity[2], 'data':identity[0], 'ora':identity[1], 'mercato':market,'prob':probability,'quota':odd,'quota_stimata':real_odd is None,'event_id':identity}
                    combos.append(pick)
                    # Le tre proposte usano prezzi presenti nel file, anche per le combo.
                    if real_odd is not None: event_options.append(pick)
        if combos:
            quoted = [c for c in combos if not c['quota_stimata']]
            result.append(max(quoted or combos, key=lambda c:c['prob']))
        daily.extend(event_options)
        for option in event_options + combos:
            option['fonte_analisi'] = 'Quote + PitchAPI (sperimentale)' if context else 'Solo quote'
    return {'listone':sorted(result, key=lambda c:(-c['prob'],c['event_id']))[:30], 'daily':daily, 'analysed':len(seen)}

def candidates(rows):
    return analyse_palinsesto(rows)['listone']

def best_ticket(events, target, max_events, max_leg_odds=None, min_events=2):
    valid = [e for e in events if number(e.get('quota')) and e['quota'] > 1.20 and e['quota'] <= target*1.05/1.20 and (max_leg_odds is None or e['quota'] <= max_leg_odds)]
    groups = {}
    # JSON converte le tuple in liste: normalizza anche le analisi ripristinate.
    for e in valid: groups.setdefault(tuple(e['event_id']),[]).append(e)
    # Ricerca compatta per quota a centesimi: mantiene i due percorsi migliori
    # per fascia e numero di eventi. I prodotti reali restano non arrotondati.
    states = [{} for _ in range(max_events+1)]
    states[0][100] = [{'legs':(), 'quota':1., 'prob':1.}]
    upper = target*1.05
    for options in groups.values():
        for size in range(max_events,0,-1):
            additions = []
            for bucket in states[size-1].values():
                for previous in bucket:
                    for e in options:
                        odd = previous['quota']*e['quota']
                        if odd > upper: continue
                        additions.append({'legs':previous['legs']+(e,), 'quota':odd,'prob':previous['prob']*e['prob']})
            for ticket in additions:
                key = round(ticket['quota']*100)
                bucket = states[size].setdefault(key,[])
                bucket.append(ticket)
                bucket.sort(key=lambda t:(-t['prob'],abs(t['quota']-target)))
                del bucket[2:]
    best = None
    for size in range(min_events,max_events+1):
        for bucket in states[size].values():
            for ticket in bucket:
                if not target*.95 <= ticket['quota'] <= upper: continue
                rank = (ticket['prob'],-abs(ticket['quota']-target),-size)
                if best is None or rank > best['rank']:
                    best = {**ticket,'rank':rank}
    return best
