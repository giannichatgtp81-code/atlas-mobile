"""Read-only historical results adapter; no bookmaker prices are replaced."""
import csv
import io
import time
import threading
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pitchapi_client import day, name, history_targets

# Keep this adapter independent of older deployed Football Charts versions.
COUNTRIES = dict(zip(
    ('inghilterra','scozia','germania','italia','spagna','francia','olanda','belgio',
     'portogallo','turchia','grecia','argentina','austria','brasile','cina','danimarca',
     'finlandia','irlanda','giappone','messico','norvegia','polonia','romania','russia',
     'svezia','svizzera','usa','stati uniti'),
    ('England','Scotland','Germany','Italy','Spain','France','Netherlands','Belgium',
     'Portugal','Turkey','Greece','Argentina','Austria','Brazil','China','Denmark',
     'Finland','Ireland','Japan','Mexico','Norway','Poland','Romania','Russia',
     'Sweden','Switzerland','USA','USA')))

def team_name(value):
    return ' '.join(t for t in name(value).split() if t not in {'cf','afc'})

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

# Explicit identities compared against the source archives. Never fuzzy-match
# across leagues, reserves, youth teams or women's competitions.
COMPETITION_ALIASES = {
    ('Japan','j league'):('JPN',True),
    ('Romania','superliga'):('ROU',True),
    ('Switzerland','superliga'):('SWZ',True),
    ('Brazil','brasileiro serie a'):('BRA',True),
    ('USA','major league soccer'):('USA',True),
}
TEAM_ALIASES = {
 'ARG': {'instituto cordoba':'instituto','union santa fe':'union de santa fe','estudiantes':'estudiantes l p'},
 'MEX': {'tigres':'tigres uanl','deportivo toluca':'toluca'},
 'JPN': {'fagiano okayama':'okayama','kyoto sanga':'kyoto','machida zelvia':'machida','urawa red diamonds':'urawa reds'},
 'RUS': {'krylia sovetov samara':'krylya sovetov','lokomotiv mosca':'lokomotiv moscow','spartak mosca':'spartak moscow','zenit st petersburg':'zenit'},
 'T1': {'amed sportif faaliyetler':'amedspor','caykur rizespor':'rizespor','erzurumspor fk':'erzurumspor','genclerbirligi sk':'genclerbirligi'},
 'D2': {'energie cottbus':'cottbus','norimberga':'nurnberg','sg dynamo dresden':'dresden','vfl 1899 osnabruck':'osnabruck'},
 'EC': {'aldershot town':'aldershot','boston united':'boston utd','carlisle united':'carlisle','forest green rovers':'forest green','halifax town':'halifax','harrogate town':'harrogate','hartlepool united':'hartlepool','kidderminster harriers':'kidderminster','scunthorpe united':'scunthorpe','solihull moors':'solihull','southend united':'southend','yeovil town':'yeovil'},
 'E0': {'manchester united':'man united'},
 'E1': {'bristol':'bristol city','charlton athletic':'charlton','derby county':'derby','lincoln city':'lincoln','west bromwich':'west brom','wolverhampton':'wolves'},
 'E2': {'bradford city':'bradford','cambridge united':'cambridge','doncaster rovers':'doncaster','luton town':'luton','mansfield town':'mansfield','oxford united':'oxford','peterborough united':'peterboro','plymouth argyle':'plymouth','sheffield wednesday':'sheffield weds','stevenage borough':'stevenage','stockport county':'stockport','wycombe wanderers':'wycombe'},
 'E3': {'accrington stanley':'accrington','bristol rovers':'bristol rvs','cheltenham town':'cheltenham','colchester united':'colchester','crawley':'crawley town','crewe alexandra':'crewe','exeter city':'exeter','grimsby town':'grimsby','northampton town':'northampton','oldham athletic':'oldham','salford city':'salford','shrewsbury town':'shrewsbury','swindon town':'swindon','tranmere rovers':'tranmere','york city':'york'},
 'F2': {'rodez aveyron football':'rodez','saint etienne':'st etienne'},
 'SP1': {'atletico bilbao':'ath bilbao','atletico madrid':'ath madrid','barcellona':'barcelona','rayo vallecano':'vallecano'},
 'POL': {'jagiellonia bialystok':'jagiellonia','kghm zaglebie lubin':'zaglebie','ks cracovia':'cracovia','slask breslavia':'slask wroclaw'},
 'SWE': {'gais goteborg':'gais'},
 'I2': {'entella':'virtus entella'},
 'D1': {'amburgo':'hamburg','bayer leverkusen':'leverkusen','bayern monaco':'bayern munich','eintracht francoforte':'ein frankfurt','lipsia':'rb leipzig','stoccarda':'stuttgart','union berlino':'union berlin'},
 'SC2': {'airdrieonians':'airdrie utd','alloa athletic':'alloa','hamilton academical':'hamilton','queen of the south':'queen of sth'},
 'SC3': {'elgin city':'elgin','forfar athletic':'forfar','stirling albion':'stirling'},
 'SC1': {'ayr united':'ayr','dunfermline athletic':'dunfermline','greenock morton':'morton','inverness':'inverness c','partick thistle':'partick','raith rovers':'raith rvs'},
 'B1': {'kaa gent':'gent','krc genk':'genk','zulte waregem':'waregem','club brugge':'brugge'},
 'SC0': {'heart of midlothian':'hearts'},
 'FIN': {'ifk mariehamn':'mariehamn','ilves tampere':'ilves'},
 'SP2': {'ad ceuta':'ceuta','ce sabadell':'sabadell','rc celta fortuna':'celta b','real sociedad b':'sociedad b'},
 'ROU': {'asc otelul galati':'otelul'},
 'N1': {'fortuna sittard':'for sittard'},
 'AUT': {'red bull salisburgo':'salzburg','wsg tirol':'tirol'},
 'F1': {'lilla':'lille','psg':'paris sg','tolosa':'toulouse','le mans':'le mans'},
 'SWZ': {'lucerna':'luzern','zurigo':'zurich'},
 'G1': {'aek atene':'aek','atromitos atene':'atromitos','ofi creta':'ofi crete'},
 'P1': {'academico de viseu':'academico viseu','maritimo madeira':'maritimo'},
 'BRA': {'clube do remo':'remo','cr vasco da gama rj':'vasco'},
}

def resolve_team(value, code, available):
    original = team_name(value)
    aliases = {team_name(k):team_name(v) for k,v in TEAM_ALIASES.get(code,{}).items()}
    candidates = {code+':'+original,code+':'+aliases.get(original,original)} & available
    # Both names existing as separate clubs is an ambiguity, never a guess.
    return next(iter(candidates)) if len(candidates) == 1 else None

def division(competition):
    parts = str(competition).split(' - ', 1)
    if len(parts) != 2:
        return None
    country = COUNTRY_NAMES.get(name(parts[0]), parts[0])
    if name(parts[0]) == 'stati uniti': country = 'USA'
    explicit = COMPETITION_ALIASES.get((country,name(parts[1])))
    if explicit: return explicit
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
    def get(self, path, ttl=86400):
        with self.lock:
            cached = self.cache.get(path)
            if cached and time.monotonic()-cached[0] < ttl: return cached[1]
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
              'fixtures_received':0,'insufficient_history':0,'unsupported':0,'unmatched':0,'errors':[], 'latest_dates':{}}
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
        teams = {m[s]['id'] for m in history for s in ('home_team','away_team')}
        home, away = resolve_team(parts[0],code,teams), resolve_team(parts[1],code,teams)
        if not home or not away or home == away:
            report['unmatched'] += 1
            continue
        report['matched'] += 1
        context = history_targets(history, {'date':cutoff,'home_team':{'id':home},'away_team':{'id':away}})
        if context:
            context['provider'] = report['provider']
            row['_pitchapi'] = context
            report['enriched'] += 1
        else: report['insufficient_history'] += 1
    return rows, report
