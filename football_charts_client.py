"""Keyless Football Charts adapter. Only public, read-only endpoints."""
import json
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pitchapi_client import day, name, history_targets


class SourceError(Exception):
    pass


class Client:
    def __init__(self):
        self.cache = {}
        self.calls = []
        self.lock = threading.RLock()
        self.rate_lock = threading.Lock()
        self.last_request = 0.0

    def get(self, path):
        with self.lock:
            now = time.monotonic()
            cached = self.cache.get(path)
            if cached and now - cached[0] < 86400:
                return cached[1]
            self.calls = [t for t in self.calls if now-t < 86400]
            if len(self.calls) >= 260:
                raise SourceError('Budget giornaliero della fonte raggiunto.')
            self.calls.append(now)
        try:
            # Space requests across all workers; avoid a burst of 93 leagues.
            with self.rate_lock:
                time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
            request = Request('https://footballcharts-backend.onrender.com/api/v1' + path,
                              headers={'Accept': 'application/json', 'User-Agent': 'AtlasMobile/1.0'})
            with urlopen(request, timeout=10) as response:
                data = json.load(response)
            if not isinstance(data, dict) or data.get('error'):
                raise SourceError('Risposta della fonte non valida.')
        except HTTPError as error:
            raise SourceError('Football Charts: HTTP ' + str(error.code) + '.') from None
        except (OSError, ValueError):
            raise SourceError('Football Charts non raggiungibile.') from None
        with self.lock:
            self.cache[path] = (time.monotonic(), data)
        return data


def result_match(match, league):
    score = re.fullmatch(r'(\d+):(\d+)', str(match.get('score', '')))
    if not score or not day(match.get('date')):
        return None
    home, away = name(match.get('homeTeam', '')), name(match.get('awayTeam', ''))
    if not home or not away:
        return None
    return {'id': str(match.get('id') or (league, match['date'], home, away)),
            'date': day(match['date']), 'status': 'finished',
            'home_team': {'id': league + ':' + home},
            'away_team': {'id': league + ':' + away},
            'score_home': int(score[1]), 'score_away': int(score[2])}


def enrich(rows, client):
    rows = [dict(r) for r in rows]
    for row in rows:
        row.pop('_pitchapi', None)
    report = {'enriched': 0, 'matched': 0, 'total': len(rows), 'fixtures_received': 0,
              'insufficient_history': 0, 'errors': [], 'provider': 'Football Charts'}
    def warning(text):
        if text not in report['errors']:
            report['errors'].append(text)
    def diagnostic(error):
        message = str(error)
        if re.fullmatch(r'Football Charts: HTTP [1-5][0-9]{2}\.', message):
            return message
        if message in {'Budget giornaliero della fonte raggiunto.', 'Football Charts non raggiungibile.', 'Risposta della fonte non valida.'}:
            return message
        return 'Formato dei dati inatteso.'
    try:
        leagues = client.get('/leagues/')['leagues']
        leagues = [l for l in leagues if re.fullmatch(r'[a-z0-9]+', l.get('league', ''))]
    except (SourceError, KeyError, TypeError):
        warning('Football Charts non disponibile: analisi solo dalle quote.')
        return rows, report
    wanted = {(day(r.get('Data')), name(p[0]), name(p[1])) for r in rows
              if len(p := str(r.get('Partita', '')).split(' - ')) == 2 and day(r.get('Data'))}
    if not wanted:
        warning('Nessuna data e coppia di squadre utilizzabile per lo studio esterno.')
        return rows, report
    index = {}
    metadata = {l['league']: l for l in leagues}
    def fixtures(league):
        return league, client.get('/leagues/' + league + '/fixtures/').get('matches', [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = [pool.submit(fixtures, l['league']) for l in leagues]
        for future in as_completed(pending):
            try:
                league, matches = future.result()
                for match in matches:
                    key = (day(match.get('match_date')), name(match.get('home_team', '')), name(match.get('away_team', '')))
                    report['fixtures_received'] += 1
                    if key in wanted:
                        index.setdefault(key, []).append((league, match))
            except (SourceError, TypeError, AttributeError) as error:
                warning('Copertura parziale: ' + diagnostic(error))
    histories = {}
    for row in rows:
        parts = str(row.get('Partita', '')).split(' - ')
        if len(parts) != 2:
            continue
        key = (day(row.get('Data')), name(parts[0]), name(parts[1]))
        matches = index.get(key, [])
        if len(matches) != 1:
            continue
        league, fixture = matches[0]
        report['matched'] += 1
        event = {'date': key[0], 'home_team': {'id': league + ':' + key[1]},
                 'away_team': {'id': league + ':' + key[2]}}
        try:
            if league not in histories:
                history = []
                # Previous season helps early-season home/away sample sizes.
                for season in metadata[league].get('seasons', [])[:2]:
                    if not re.fullmatch(r'\d{4}(?:-\d{4})?', season):
                        continue
                    try:
                        raw = client.get('/leagues/' + league + '/results/?season=' + season).get('matches', [])
                        history.extend(m for r in raw if (m := result_match(r, league)))
                    except (SourceError, TypeError, KeyError, AttributeError) as error:
                        warning('Storico ' + league + ' (' + season + '): ' + diagnostic(error))
                        # Keep every season already received, even if another fails.
                        continue
                histories[league] = history
            context = history_targets(histories[league], event)
            if context:
                context['provider'] = 'Football Charts'
                row['_pitchapi'] = context
                report['enriched'] += 1
            else:
                report['insufficient_history'] += 1
        except (SourceError, TypeError, KeyError, AttributeError):
            histories[league] = []
            warning('Storico di alcuni campionati non disponibile: nessun dato inventato.')
    if not report['enriched']:
        warning('Nessuno storico utilizzabile: le proposte restano basate solo sulle quote.')
    return rows, report
