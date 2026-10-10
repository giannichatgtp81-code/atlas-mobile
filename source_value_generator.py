"""Source-only predictions. Prices never enter the probability model."""
import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from football_data_client import CATALOG, record
from pitchapi_client import day, history_targets
from manual_stats import context_for, load as load_manual_stats, render_comparison

MARKETS = {'1':'B365H','X':'B365D','2':'B365A','Over 2.5':'B365>2.5','Under 2.5':'B365<2.5'}
DIVISIONS = {code:country+' - '+label for country,entries in CATALOG.items() for code,label in entries}
MAIN_DIVISIONS = ('I1','E0','SP1','D1','F1','P1','N1','T1','I2','E1')

def price(value):
    try: p = float(value)
    except (TypeError,ValueError): return None
    return p if math.isfinite(p) and p > 1 else None

def kickoff(row, source_timezone='Europe/London'):
    date = day(row.get('Date'))
    if not date: return None
    try: return datetime.strptime(date+' '+str(row.get('Time')), '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(source_timezone))
    except (ValueError,TypeError): return None

def select_ticket(picks,count,max_per_league=None):
    if type(count) is not int or not 1 <= count <= 30: raise ValueError('Scegli da 1 a 30 eventi.')
    if max_per_league is not None and (type(max_per_league) is not int or not 1 <= max_per_league <= 30): raise ValueError('Limite campionato non valido.')
    unique = {}
    for p in picks:
        identity = p['event_id']
        if identity not in unique or (p['edge'],p['prob']) > (unique[identity]['edge'],unique[identity]['prob']): unique[identity]=p
    legs, leagues = [], {}
    for p in sorted(unique.values(),key=lambda p:(-p['edge'],-p['prob'],p['event_id'])):
        league = p['event_id'][0]
        if max_per_league is not None and leagues.get(league,0) >= max_per_league: continue
        legs.append(p); leagues[league] = leagues.get(league,0)+1
        if len(legs) == count: break
    return {'legs':legs,'requested':count,'quota':math.prod(p['quota'] for p in legs) if legs else None,
            'prob':math.prod(p['prob'] for p in legs) if legs else None}

def generate(client,reference_date,now=None,min_edge=.05,source_timezone='Europe/London',allowed_divisions=None,min_probability=0.0,max_history_age=30,manual_snapshots=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 0 <= min_edge <= 1: raise ValueError('Parametri non validi.')
    if not 0 <= min_probability <= 1 or not 1 <= max_history_age <= 60: raise ValueError('Filtri non validi.')
    allowed = set(DIVISIONS) if allowed_divisions is None else set(allowed_divisions)&set(DIVISIONS)
    ref = day(str(reference_date))
    if not ref: raise ValueError('Data non valida.')
    report = {'received':0,'scheduled':0,'analysed':0,'insufficient_history':0,'no_value':0,'missing_quotes':0,
              'errors':[],'source':'Football-Data.co.uk','bookmaker':'Bet365','quote_checked_at':now.isoformat(),
              'min_edge':min_edge,'source_timezone':source_timezone,'reference_date':ref}
    report.update(min_probability=min_probability,max_history_age=max_history_age,stale_history=0,low_probability=0,allowed_divisions=sorted(allowed))
    report['manual_context'] = []
    picks,histories,seen = [],{},set()
    if not allowed: return {'picks':picks,'report':report}
    try: fixtures = client.get('fixtures.csv',ttl=3600)
    except (OSError,ValueError):
        report['errors'].append('Palinsesto della fonte non disponibile. Nessuna proposta inventata.')
        return {'picks':picks,'report':report}
    report['received']=len(fixtures)
    started=time.monotonic()
    for fixture in fixtures:
        code=fixture.get('Div'); when=kickoff(fixture,source_timezone)
        if code not in allowed or not when or when.date().isoformat()!=ref or when <= now: continue
        identity=(code,ref,str(fixture.get('HomeTeam')),str(fixture.get('AwayTeam')))
        if identity in seen: continue
        seen.add(identity); report['scheduled']+=1
        manual_context = context_for(identity[2], identity[3], ref, manual_snapshots, now)
        if manual_context:
            report['manual_context'].append({'partita':identity[2]+' - '+identity[3], 'statistiche':manual_context,
                'stato':'Solo contesto: mancano quote reali BTTS/corner e un modello validato per questi mercati.'})
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
        recent = []
        for side in ('home_team','away_team'):
            dates = [m['date'] for m in histories[key] if m['date'] < ref and
                     any(m[s]['id']==event[side]['id'] for s in ('home_team','away_team'))]
            recent.append(max(dates) if dates else None)
        if any(d is None or (when.date()-datetime.fromisoformat(d).date()).days > max_history_age for d in recent):
            report['stale_history']+=1; continue
        report['analysed']+=1
        targets=dict(context['targets']); targets['Under 2.5']=1-targets['Over 2.5']
        options=[]; quoted=False
        for market,column in MARKETS.items():
            odd=price(fixture.get(column))
            if odd is None or odd <= 1.20: continue
            quoted=True; probability=targets[market]; edge=probability*odd-1
            if edge < min_edge or not 0 < probability < 1: continue
            if probability < min_probability:
                report['low_probability']+=1; continue
            options.append({'event_id':identity,'partita':identity[2]+' - '+identity[3],
                'competition':DIVISIONS[code],'mercato':market,'quota':odd,'prob':probability,
                'edge':edge,'fair_odds':1/probability,'kickoff':when.isoformat(),
                'home_matches':context['home_matches'],'away_matches':context['away_matches']})
            options[-1]['statistiche_aggiuntive'] = manual_context
        if options: picks.append(max(options,key=lambda p:(p['edge'],p['prob'])))
        elif quoted: report['no_value']+=1
        else: report['missing_quotes']+=1
    return {'picks':picks,'report':report}

def backtest_history(matches,max_matches=100):
    """Walk-forward scoring. Each prediction sees only strictly earlier days."""
    settled = {m['id']:m for m in matches if m.get('status')=='finished' and
               type(m.get('score_home')) is int and type(m.get('score_away')) is int}
    ordered = sorted(settled.values(),key=lambda m:m['date'])
    scores, over_scores = [], []
    for event in ordered[-max_matches:]:
        prior = [m for m in ordered if m['date'] < event['date']]
        context = history_targets(prior,event)
        if not context: continue
        h,a = event['score_home'],event['score_away']
        actual = '1' if h>a else '2' if h<a else 'X'
        scores.append(sum((context['targets'][m]-int(m==actual))**2 for m in ('1','X','2'))/3)
        over_scores.append((context['targets']['Over 2.5']-int(h+a>2))**2)
    return {'matches':len(scores),'brier_1x2':sum(scores)/len(scores) if scores else None,
            'brier_over25':sum(over_scores)/len(over_scores) if over_scores else None,
            'baseline_uniform_1x2':2/9,'baseline_over25':.25}

def render(client):
    import streamlit as st
    today=datetime.now(ZoneInfo('Europe/Budapest')).date()
    with st.expander('Generatore autonomo · senza PDF'):
        st.caption('Selezione per valore stimato più alto: probabilità × quota − 1. Non significa maggiore probabilità di vincita.')
        st.caption('Probabilità solo dallo storico. Quote Bet365 dalla fonte Football-Data.co.uk, non in tempo reale. Mercati: 1X2 e Under/Over 2,5. Copertura limitata alle partite con quote disponibili.')
        reference=st.date_input('Giornata da studiare',value=today,min_value=today,key='value_date')
        count=st.slider('Eventi della giocata',1,30,1,key='value_count')
        leagues=st.multiselect('Campionati da includere',list(DIVISIONS),default=list(MAIN_DIVISIONS),format_func=lambda c:DIVISIONS[c],key='value_leagues')
        probability=st.slider('Probabilità stimata minima per partita (%)',50,90,65,key='value_probability')
        cap=st.slider('Massimo eventi per campionato',1,30,2,key='value_league_cap')
        with st.expander('Impostazioni'):
            edge=st.number_input('Valore stimato minimo (%)',0.0,100.0,5.0,1.0,key='value_edge')
            tz=st.selectbox('Fuso orari della fonte',('Europe/London','Europe/Budapest','UTC'),key='value_timezone')
            st.caption('Il filtro delle partite iniziate usa questo fuso. Controlla orari e quote sul bookmaker.')
        cached_manual = st.cache_data(ttl=60)(load_manual_stats)
        snapshots = [data for kind in ('btts', 'corners') if (data := st.session_state.get('manual_' + kind) or cached_manual(kind))]
        signature=(reference.isoformat(),edge,tz,tuple(sorted(leagues)),probability,
                   tuple((d.get('tipo'),d.get('caricato_il'),d.get('pagina_salvata_il')) for d in snapshots))
        if st.button('Studia le partite e genera',key='value_generate'):
            st.session_state.pop('value_result',None)
            try:
                with st.spinner('Studio dello storico e confronto delle quote…'): result=generate(client,reference,min_edge=edge/100,source_timezone=tz,allowed_divisions=leagues,min_probability=probability/100,max_history_age=30,manual_snapshots=snapshots)
                st.session_state['value_result']=(signature,result)
            except Exception: st.error('Generazione non riuscita. Nessun risultato precedente viene mostrato come nuovo.')
        saved=st.session_state.get('value_result')
        if not saved: return
        if saved[0]!=signature:
            st.info('Impostazioni cambiate: rigenera le proposte.'); return
        result=saved[1]; report=result['report']
        eligible=[p for p in result['picks'] if datetime.fromisoformat(p['kickoff']) > datetime.now(timezone.utc)]
        ticket=select_ticket(eligible,count,max_per_league=cap)
        st.caption(f"{report['analysed']} partite studiate · {len(eligible)} proposte di valore. Lettura: {report['quote_checked_at']}.")
        for message in report['errors']: st.warning(message)
        contexts = report.get('manual_context', [])
        st.caption(f"Statistiche HTML abbinate a {len(contexts)} partite della giornata. Non alterano automaticamente le probabilità 1X2 o gol.")
        if contexts:
            with st.expander('Confronto sportivo BTTS e corner · dati descrittivi'):
                for item in contexts:
                    st.write(item['partita'])
                    render_comparison(item['statistiche'])
                st.info('Confronto descrittivo indipendente: non seleziona giocate e non modifica le multiple.')
        if not ticket['legs']:
            st.info('Nessuna proposta valida con i dati disponibili. Non uso il PDF come ripiego.'); return
        if len(ticket['legs'])<count: st.warning(f"Solo {len(ticket['legs'])} eventi validi su {count} richiesti: non completo a forza.")
        for p in ticket['legs']:
            st.write(f"**{p['partita']}** — {p['mercato']} · quota {p['quota']:.2f}")
            st.caption(f"Probabilità stimata {p['prob']*100:.1f}% · valore stimato +{p['edge']*100:.1f}% · {p['competition']}")
        st.write(f"Quota totale {ticket['quota']:.2f} · probabilità della multipla stimata {ticket['prob']*100:.2f}%")
        st.caption('La soglia scelta riguarda ciascuna partita, non la multipla: aggiungere eventi ne riduce la probabilità complessiva.')
        st.caption('Modello sperimentale non validato, nessuna vincita garantita. Verifica le quote attuali: quelle pubblicate possono essere superate. La multipla assume eventi indipendenti; con 30 eventi la probabilità complessiva può essere molto bassa. Nessuna puntata viene effettuata.')
