"""Conservative PitchAPI enrichment; no credentials or raw errors in outputs."""
import json
import re
import time
import unicodedata
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class PitchError(Exception):
    pass


def day(value):
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def name(value):
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    tokens = re.findall(r'[a-z0-9]+', text)
    return ' '.join(t for t in tokens if t not in {'fc', 'sc', 'club'})


class Client:
    def __init__(self, key):
        self.key = str(key).strip()
        self.cache = {}

    def get(self, path):
        cached = self.cache.get(path)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        request = Request('https://api.pitchapi.dev' + path,
                          headers={'X-API-KEY': self.key, 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                envelope = json.load(response)
            data = envelope.get('data') if isinstance(envelope, dict) else None
            if not isinstance(data, dict):
                raise PitchError('Risposta PitchAPI non valida.')
        except HTTPError as error:
            if error.code in (401, 403):
                raise PitchError('Chiave PitchAPI non valida o accesso non autorizzato.') from None
            if error.code == 429:
                raise PitchError('PitchAPI: limite di richieste raggiunto (HTTP 429).') from None
            if error.code == 404:
                raise PitchError('PitchAPI: endpoint o risorsa non trovata (HTTP 404).') from None
            raise PitchError('PitchAPI: errore del servizio (HTTP ' + str(error.code) + ').') from None
        except (OSError, ValueError):
            raise PitchError('PitchAPI temporaneamente non disponibile.') from None
        self.cache[path] = (time.monotonic(), data)
        return data


def history_targets(matches, fixture):
    """Only settled scores strictly before kickoff day; no future leakage."""
    home = fixture['home_team']['id']
    away = fixture['away_team']['id']
    cutoff = day(fixture.get('date'))
    if not cutoff or not home or not away or home == away:
        return None
    settled = []
    seen = set()
    for match in matches:
        date = day(match.get('date'))
        h, a = match.get('score_home'), match.get('score_away')
        if (match.get('status') != 'finished' or not date or date >= cutoff
                or type(h) is not int or type(a) is not int or min(h, a) < 0):
            continue
        identity = match.get('id') or (date, match.get('home_team', {}).get('id'), match.get('away_team', {}).get('id'))
        if identity in seen:
            continue
        seen.add(identity)
        settled.append(match)
    host = sorted((m for m in settled if m.get('home_team', {}).get('id') == home), key=lambda m: m['date'])[-10:]
    visitor = sorted((m for m in settled if m.get('away_team', {}).get('id') == away), key=lambda m: m['date'])[-10:]
    if min(len(host), len(visitor)) < 5 or len(settled) < 20:
        return None
    cutoff_date = datetime.strptime(cutoff, '%Y-%m-%d').date()
    if any((cutoff_date - datetime.strptime(day(sample[-1]['date']), '%Y-%m-%d').date()).days > 60
           for sample in (host, visitor)):
        return None
    # Shrink small team samples toward league averages instead of extreme rates.
    base_h = sum(m['score_home'] for m in settled) / len(settled)
    base_a = sum(m['score_away'] for m in settled) / len(settled)
    def rate(sample, field, base):
        return (sum(m[field] for m in sample) + 5 * base) / (len(sample) + 5)
    lh = (rate(host, 'score_home', base_h) + rate(visitor, 'score_home', base_h)) / 2
    la = (rate(host, 'score_away', base_a) + rate(visitor, 'score_away', base_a)) / 2
    from math import exp, factorial
    cells = [(h, a, exp(-lh-la) * lh**h * la**a / factorial(h) / factorial(a))
             for h in range(16) for a in range(16)]
    from mobile_combo_engine import holds
    total = sum(p for h, a, p in cells)
    targets = {m: sum(p for h, a, p in cells if holds(m, h, a)) / total
               for m in ('1', 'X', '2', 'Goal', 'Over 1.5', 'Over 2.5', 'Over 3.5')}
    return {'targets': targets, 'home_matches': len(host), 'away_matches': len(visitor)}


def enrich(rows, client):
    enriched = [dict(row) for row in rows]
    for row in enriched:
        row.pop('_pitchapi', None)
    report = {'matched': 0, 'enriched': 0, 'total': len(rows), 'errors': [], 'experimental': True,
              'fixtures_received': 0, 'insufficient_history': 0, 'unmatched': 0}
    if client is None:
        report['errors'] = ['PitchAPI non configurata: analisi basata solo sulle quote.']
        return enriched, report
    started = time.monotonic()
    dates = {}
    leagues = {}
    failed_dates, failed_leagues = set(), set()
    for row in enriched:
        if time.monotonic() - started > 45:
            report['errors'].append('Limite di tempo raggiunto: copertura parziale.')
            break
        date = day(row.get('Data'))
        parts = str(row.get('Partita', '')).split(' - ')
        if not date or len(parts) != 2:
            continue
        if date in failed_dates:
            continue
        stage = 'palinsesto del ' + date
        league = None
        try:
            if date not in dates:
                dates[date] = client.get('/v1/date/' + date + '?status=all').get('matches', [])
                if not isinstance(dates[date], list):
                    raise PitchError('Risposta PitchAPI non valida.')
                report['fixtures_received'] += len(dates[date])
            fixtures = [m for m in dates[date]
                        if day(m.get('date')) == date
                        and name(m.get('home_team', {}).get('name')) == name(parts[0])
                        and name(m.get('away_team', {}).get('name')) == name(parts[1])]
            if len(fixtures) != 1:
                report['unmatched'] += 1
                continue
            fixture = fixtures[0]
            report['matched'] += 1
            league = fixture.get('league', {}).get('id')
            if not league or not re.fullmatch(r'l_[A-Za-z0-9]{6}', league):
                continue
            stage = 'storico del campionato'
            if league in failed_leagues:
                continue
            if league not in leagues:
                leagues[league] = client.get('/v1/leagues/' + league + '/matches').get('matches', [])
            context = history_targets(leagues[league], fixture)
            if context:
                row['_pitchapi'] = context
                report['enriched'] += 1
            else:
                report['insufficient_history'] += 1
        except (PitchError, KeyError, TypeError, AttributeError, ValueError) as error:
            # Never include arbitrary exception strings, response bodies or keys.
            detail = 'Formato dei dati inatteso.'
            safe_messages = {'Risposta PitchAPI non valida.',
                             'Chiave PitchAPI non valida o accesso non autorizzato.',
                             'PitchAPI temporaneamente non disponibile.',
                             'PitchAPI: limite di richieste raggiunto (HTTP 429).',
                             'PitchAPI: endpoint o risorsa non trovata (HTTP 404).'}
            if isinstance(error, PitchError):
                message = str(error)
                if message in safe_messages or re.fullmatch(r'PitchAPI: errore del servizio \(HTTP [1-5][0-9]{2}\)\.', message):
                    detail = message
            diagnostic = stage + ': ' + detail
            if diagnostic not in report['errors']:
                report['errors'].append(diagnostic)
            if league is None:
                failed_dates.add(date)
            else:
                failed_leagues.add(league)
            if detail == 'Chiave PitchAPI non valida o accesso non autorizzato.' or '429' in detail:
                break
    if not report['enriched'] and not report['errors']:
        report['errors'].append('Nessuno storico utilizzabile: verificare copertura, nomi delle squadre e quantità di risultati precedenti.')
    return enriched, report
