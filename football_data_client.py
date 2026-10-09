"""Read-only historical results adapter; no bookmaker prices are replaced."""
import csv
import io
import time
import threading
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from football_charts_client import COUNTRIES, team_name
from pitchapi_client import day, name, history_targets

# Source division identifiers, not an assertion of current availability.
CATALOG = {
    'England': [('E0','premier league'),('E1','championship'),('E2','league one'),('E3','league two'),('EC','national league')],
    'Scotland': [('SC0','premiership'),('SC1','championship'),('SC2','league one'),('SC3','league two')],
    'Germany': [('D1','bundesliga'),('D2','2 bundesliga')],
    'Italy': [('I1','serie a'),('I2','serie b')],
    'Spain': [('SP1','laliga'),('SP2','laliga 2')],
    'France': [('F1','ligue 1'),('F2','ligue 2')],
    'Netherlands': [('N1','eredivisie')], 'Belgium': [('B1','pro league')],
    'Portugal': [('P1','primeira liga')], 'Turkey': [('T1','super lig')],
    'Greece': [('G1','super league')],
}
EXTRAS = dict(zip(
    ('Argentina','Austria','Brazil','China','Denmark','Finland','Ireland','Japan','Mexico','Norway','Poland','Romania','Russia','Sweden','Switzerland','USA'),
    ('ARG','AUT','BRA','CHN','DNK','FIN','IRL','JPN','MEX','NOR','POL','ROU','RUS','SWE','SWZ','USA')))
EXTRA_LABELS = dict(zip(EXTRAS, ('liga profesional','bundesliga','serie a','super league','superligaen','veikkausliiga','premier division','j1 league','liga mx','eliteserien','ekstraklasa','liga 1','premier league','allsvenskan','super league','mls')))
ALIASES = {'liga':'laliga','la liga':'laliga','segunda division':'laliga 2',
           'liga portugal':'primeira liga','super lig':'super lig',
           'jupiler pro league':'pro league','super league 1':'super league',
           'superliga':'superligaen'}
COUNTRY_NAMES = dict(COUNTRIES, scozia='Scotland', grecia='Greece', argentina='Argentina',
                     cina='China', giappone='Japan', messico='Mexico', usa='USA')

def division(competition):
    parts = str(competition).split(' - ', 1)
    if len(parts) != 2:
        return None
    country = COUNTRY_NAMES.get(name(parts[0]), parts[0])
    if name(parts[0]) == 'stati uniti': country = 'USA'
    label = ALIASES.get(name(parts[1]), name(parts[1]))
    for code, expected in CATALOG.get(country, []):
        if label == expected: return code, False
    if country in EXTRAS and label == EXTRA_LABELS[country]:
        return EXTRAS[country], True
    return None

def record(row, code):
    date = None
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try:
            date = datetime.strptime(str(row.get('Date')), fmt).date().isoformat()
            break
        except ValueError: pass
    home, away = team_name(row.get('HomeTeam') or row.get('Home') or ''), team_name(row.get('AwayTeam') or row.get('Away') or '')
    h, a = str(row.get('FTHG', row.get('HG', ''))), str(row.get('FTAG', row.get('AG', '')))
    if not date or not home or not away or home == away or not h.isdigit() or not a.isdigit(): return None
    return {'id': (code,date,home,away), 'date':date, 'status':'finished',
            'home_team':{'id':code+':'+home}, 'away_team':{'id':code+':'+away},
            'score_home':int(h), 'score_away':int(a)}

class Client:
    def __init__(self):
        self.cache = {}
        self.lock = threading.RLock()
    def get(self, path):
        with self.lock:
            cached = self.cache.get(path)
            if cached and time.monotonic()-cached[0] < 86400: return cached[1]
            request = Request('https://football-data.co.uk/'+path, headers={'User-Agent':'AtlasMobile/1.0','Accept':'text/csv'})
            with urlopen(request, timeout=10) as response:
                text = response.read(8_000_001)
            if len(text) > 8_000_000: raise ValueError('Archive too large')
            rows = list(csv.DictReader(io.StringIO(text.decode('utf-8-sig'))))
            if not rows or 'Date' not in rows[0]: raise ValueError('Invalid CSV')
            self.cache[path] = time.monotonic(), rows
            return rows

def enrich(rows, client):
    rows = [dict(r) for r in rows]
    report = {'provider':'Football-Data.co.uk','total':len(rows),'enriched':0,'matched':0,
              'fixtures_received':0,'insufficient_history':0,'unsupported':0,'errors':[], 'latest_dates':{}}
    histories = {}
    start = time.monotonic()
    for row in rows:
        row.pop('_pitchapi', None)
        spec = division(row.get('Competizione'))
        cutoff = day(row.get('Data'))
        parts = str(row.get('Partita','')).split(' - ')
        if not spec or not cutoff or len(parts) != 2:
            report['unsupported'] += 1
            continue
        code, extra = spec
        year = int(cutoff[:4]) - (int(cutoff[5:7]) < 7)
        key = code, year
        if key not in histories:
            histories[key] = []
            paths = ['new/'+code+'.csv'] if extra else [f'mmz4281/{y%100:02d}{(y+1)%100:02d}/{code}.csv' for y in (year, year-1)]
            for path in paths:
                if time.monotonic()-start > 60:
                    message = 'Tempo di lettura raggiunto: copertura parziale.'
                    if message not in report['errors']: report['errors'].append(message)
                    break
                try:
                    histories[key].extend(m for r in client.get(path) if (m := record(r,code)))
                except (HTTPError, OSError, ValueError, UnicodeError, csv.Error):
                    report['errors'].append('Archivio '+code+' non disponibile ('+path+').')
            report['fixtures_received'] += len(histories[key])
        history = [m for m in histories[key] if m['date'] < cutoff]
        if history: report['latest_dates'][code] = max(m['date'] for m in history)
        home, away = code+':'+team_name(parts[0]), code+':'+team_name(parts[1])
        teams = {m[s]['id'] for m in history for s in ('home_team','away_team')}
        if home not in teams or away not in teams: continue
        report['matched'] += 1
        context = history_targets(history, {'date':cutoff,'home_team':{'id':home},'away_team':{'id':away}})
        if context:
            context['provider'] = report['provider']
            row['_pitchapi'] = context
            report['enriched'] += 1
        else: report['insufficient_history'] += 1
    return rows, report
