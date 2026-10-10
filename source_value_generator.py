"""Source-only predictions. Prices never enter the probability model."""
import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from football_data_client import CATALOG, record
from pitchapi_client import day, history_targets

MARKETS = {'1':'B365H','X':'B365D','2':'B365A','Over 2.5':'B365>2.5','Under 2.5':'B365<2.5'}
DIVISIONS = {code:country+' - '+label for country,entries in CATALOG.items() for code,label in entries}

def price(value):
    try: p = float(value)
    except (TypeError,ValueError): return None
    return p if math.isfinite(p) and p > 1 else None

def kickoff(row, source_timezone='Europe/London'):
    date = day(row.get('Date'))
    if not date: return None
    try: return datetime.strptime(date+' '+str(row.get('Time')), '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(source_timezone))
    except (ValueError,TypeError): return None

def select_ticket(picks,count):
    if type(count) is not int or not 1 <= count <= 30: raise ValueError('Scegli da 1 a 30 eventi.')
    unique = {}
    for p in picks:
        identity = p['event_id']
        if identity not in unique or (p['prob'],p['edge']) > (unique[identity]['prob'],unique[identity]['edge']): unique[identity]=p
    legs = sorted(unique.values(),key=lambda p:(-p['prob'],-p['edge'],p['event_id']))[:count]
    return {'legs':legs,'requested':count,'quota':math.prod(p['quota'] for p in legs) if legs else None,
            'prob':math.prod(p['prob'] for p in legs) if legs else None}

def generate(client,reference_date,now=None,min_edge=.05,source_timezone='Europe/London'):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 0 <= min_edge <= 1: raise ValueError('Parametri non validi.')
    ref = day(str(reference_date))
    if not ref: raise ValueError('Data non valida.')
    report = {'received':0,'scheduled':0,'analysed':0,'insufficient_history':0,'no_value':0,'missing_quotes':0,
              'errors':[],'source':'Football-Data.co.uk','bookmaker':'Bet365','quote_checked_at':now.isoformat(),
              'min_edge':min_edge,'source_timezone':source_timezone,'reference_date':ref}
    picks,histories,seen = [],{},set()
    try: fixtures = client.get('fixtures.csv',ttl=3600)
    except (OSError,ValueError):
        report['errors'].append('Palinsesto della fonte non disponibile. Nessuna proposta inventata.')
        return {'picks':picks,'report':report}
    report['received']=len(fixtures)
    started=time.monotonic()
    for fixture in fixtures:
        code=fixture.get('Div'); when=kickoff(fixture,source_timezone)
        if code not in DIVISIONS or not when or when.date().isoformat()!=ref or when <= now: continue
        identity=(code,ref,str(fixture.get('HomeTeam')),str(fixture.get('AwayTeam')))
        if identity in seen: continue
        seen.add(identity); report['scheduled']+=1
        # Extract identities only. This fixture never enters the history sample.
        parsed=record(dict(fixture,FTHG='0',FTAG='0'),code)
        if not parsed: continue
        event={k:parsed[k] for k in ('date','home_team','away_team')}
        year=when.year-(when.month < 7); key=code,year
        if key not in histories:
            if time.monotonic()-started > 60:
                report['errors'].append('Tempo di lettura raggiunto: copertura parziale.'); break
            histories[key]=[]
            for y in (year,year-1):
                try: histories[key].extend(m for r in client.get(f'mmz4281/{y%100:02d}{(y+1)%100:02d}/{code}.csv') if (m:=record(r,code)))
                except (OSError,ValueError): report['errors'].append('Storico '+code+' non disponibile per una stagione.')
        context=history_targets(histories[key],event)
        if not context:
            report['insufficient_history']+=1; continue
        report['analysed']+=1
        targets=dict(context['targets']); targets['Under 2.5']=1-targets['Over 2.5']
        options=[]; quoted=False
        for market,column in MARKETS.items():
            odd=price(fixture.get(column))
            if odd is None or odd <= 1.20: continue
            quoted=True; probability=targets[market]; edge=probability*odd-1
            if edge < min_edge or not 0 < probability < 1: continue
            options.append({'event_id':identity,'partita':identity[2]+' - '+identity[3],
                'competition':DIVISIONS[code],'mercato':market,'quota':odd,'prob':probability,
                'edge':edge,'fair_odds':1/probability,'kickoff':when.isoformat(),
                'home_matches':context['home_matches'],'away_matches':context['away_matches']})
        if options: picks.append(max(options,key=lambda p:(p['prob'],p['edge'])))
        elif quoted: report['no_value']+=1
        else: report['missing_quotes']+=1
    return {'picks':picks,'report':report}

def render(client):
    import streamlit as st
    today=datetime.now(ZoneInfo('Europe/Budapest')).date()
    with st.expander('Generatore autonomo · senza PDF'):
        st.caption('Probabilità solo dallo storico. Quote Bet365 dalla fonte Football-Data.co.uk, non in tempo reale. Mercati: 1X2 e Under/Over 2,5. Copertura limitata alle partite con quote disponibili.')
        reference=st.date_input('Giornata da studiare',value=today,min_value=today,key='value_date')
        count=st.slider('Eventi della giocata',1,30,1,key='value_count')
        with st.expander('Impostazioni'):
            edge=st.number_input('Valore stimato minimo (%)',0.0,100.0,5.0,1.0,key='value_edge')
            tz=st.selectbox('Fuso orari della fonte',('Europe/London','Europe/Budapest','UTC'),key='value_timezone')
            st.caption('Il filtro delle partite iniziate usa questo fuso. Controlla orari e quote sul bookmaker.')
        signature=(reference.isoformat(),edge,tz)
        if st.button('Studia le partite e genera',key='value_generate'):
            st.session_state.pop('value_result',None)
            try:
                with st.spinner('Studio dello storico e confronto delle quote…'): result=generate(client,reference,min_edge=edge/100,source_timezone=tz)
                st.session_state['value_result']=(signature,result)
            except Exception: st.error('Generazione non riuscita. Nessun risultato precedente viene mostrato come nuovo.')
        saved=st.session_state.get('value_result')
        if not saved: return
        if saved[0]!=signature:
            st.info('Impostazioni cambiate: rigenera le proposte.'); return
        result=saved[1]; report=result['report']
        eligible=[p for p in result['picks'] if datetime.fromisoformat(p['kickoff']) > datetime.now(timezone.utc)]
        ticket=select_ticket(eligible,count)
        st.caption(f"{report['analysed']} partite studiate · {len(eligible)} proposte di valore. Lettura: {report['quote_checked_at']}.")
        for message in report['errors']: st.warning(message)
        if not ticket['legs']:
            st.info('Nessuna proposta valida con i dati disponibili. Non uso il PDF come ripiego.'); return
        if len(ticket['legs'])<count: st.warning(f"Solo {len(ticket['legs'])} eventi validi su {count} richiesti: non completo a forza.")
        for p in ticket['legs']:
            st.write(f"**{p['partita']}** — {p['mercato']} · quota {p['quota']:.2f}")
            st.caption(f"Probabilità stimata {p['prob']*100:.1f}% · valore stimato +{p['edge']*100:.1f}% · {p['competition']}")
        st.write(f"Quota totale {ticket['quota']:.2f} · probabilità congiunta stimata {ticket['prob']*100:.2f}%")
        st.caption('Modello sperimentale non validato, nessuna vincita garantita. Verifica le quote attuali: quelle pubblicate possono essere superate. La multipla assume eventi indipendenti; con 30 eventi la probabilità complessiva può essere molto bassa. Nessuna puntata viene effettuata.')
