"""Importazione offline: nessun HTML o script viene eseguito o pubblicato."""
import base64
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError
from analysis_storage import api, BRANCH


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.table = []
        elif tag == 'tr' and self.table is not None: self.row = []
        elif tag in ('td', 'th') and self.row is not None: self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse(content, kind, now=None):
    if kind not in ('btts', 'corners'): raise ValueError('Tipo non valido.')
    if len(content) > 5_000_000: raise ValueError('File troppo grande: massimo 5 MB.')
    parser = Tables()
    parser.feed(content.decode('utf-8-sig', errors='replace'))
    rows = []
    for table in parser.tables:
        if not table: continue
        header = ' '.join(table[0]).lower()
        if 'squadra' not in header: continue
        if kind == 'btts' and 'btts' not in header: continue
        if kind == 'corners' and ('media corners' not in header or '9.5' in header): continue
        for cells in table[1:]:
            if len(cells) != 5: continue
            _, team, value, extra, _ = cells
            if not team: continue
            if kind == 'btts':
                sample = re.fullmatch(r'(\d+)\s*/\s*(\d+)\s*partite', value)
                percent = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*%', extra)
                if not sample or not percent: continue
                hits, games = map(int, sample.groups())
                pct = float(percent[1].replace(',', '.'))
                if not 0 <= hits <= games or games == 0 or not 0 <= pct <= 100: continue
                if abs(pct - hits / games * 100) > 1.1: continue
                rows.append(dict(squadra=team, btts_partite=hits, partite=games, percentuale=pct))
            else:
                average = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*/\s*partita', value)
                if not average: continue
                avg = float(average[1].replace(',', '.'))
                if not 0 <= avg <= 40: continue
                rows.append(dict(squadra=team, media_corner_totali=avg))
        break
    if not rows: raise ValueError('Tabella richiesta non trovata nel file salvato.')
    return dict(version=1, tipo=kind, fonte='FootyStats — HTML caricato manualmente',
                caricato_il=(now or datetime.now(timezone.utc)).isoformat(), righe=rows)


def stale(data, now=None):
    try:
        stamp = datetime.fromisoformat(data['caricato_il'])
        return stamp.tzinfo is None or (now or datetime.now(timezone.utc)) - stamp >= timedelta(days=7)
    except (KeyError, TypeError, ValueError): return True


def context_for(home, away, reference_date, snapshots, now=None):
    """Exact normalized names only; ambiguous rows and undated snapshots are excluded."""
    def name(value):
        text = unicodedata.normalize('NFKD', str(value)).casefold()
        return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))
    result = []
    for data in snapshots or []:
        if not isinstance(data, dict) or stale(data, now): continue
        try:
            saved_date = datetime.fromisoformat(data['pagina_salvata_il']).date()
            ref = datetime.fromisoformat(str(reference_date)).date()
        except (KeyError, TypeError, ValueError): continue
        if not 0 <= (ref - saved_date).days < 7: continue
        for side, team in [('casa', home), ('trasferta', away)]:
            matches = [r for r in data.get('righe', []) if name(r.get('squadra', '')) == name(team)]
            if len(matches) == 1:
                result.append(dict(tipo=data['tipo'], lato=side, dati=matches[0],
                                   pagina_salvata_il=data['pagina_salvata_il']))
    return result


def comparison_rows(context):
    """Descriptive statistics only: no match prediction or betting ranking."""
    rows = []
    for entry in context:
        data = entry['dati']
        row = {'Squadra': data['squadra'], 'Lato partita': entry['lato'],
               'Pagina salvata': entry['pagina_salvata_il']}
        if entry['tipo'] == 'btts':
            hits, games = data['btts_partite'], data['partite']
            if not 0 <= hits <= games or games <= 0: continue
            p = hits / games
            z = 1.96
            denominator = 1 + z*z/games
            center = (p + z*z/(2*games))/denominator
            width = z*((p*(1-p)/games + z*z/(4*games*games))**.5)/denominator
            row.update(Statistica='Entrambe hanno segnato', Frequenza=f'{hits}/{games} ({p*100:.1f}%)',
                       Campione=games, **{'Intervallo descrittivo 95%':f'{max(0,center-width)*100:.1f}–{min(1,center+width)*100:.1f}%'})
        elif entry['tipo'] == 'corners':
            row.update(Statistica='Media corner totali nelle partite della squadra',
                       Frequenza=str(data['media_corner_totali']), Campione='Non disponibile',
                       **{'Intervallo descrittivo 95%':'Non calcolabile'})
        else: continue
        rows.append(row)
    return rows


def render_comparison(context):
    import streamlit as st
    rows = comparison_rows(context)
    if rows: st.dataframe(rows, hide_index=True)
    sides = {e['lato'] for e in context if e['tipo'] == 'btts'}
    if sides != {'casa', 'trasferta'}:
        st.caption('BTTS: manca il dato di almeno una delle due squadre. Nessun valore sostitutivo viene inventato.')
    st.caption('Il lato casa/trasferta identifica la partita da confrontare: le frequenze importate non sono statistiche separate casa/trasferta. Gli intervalli BTTS descrivono l’incertezza del campione con ipotesi binomiale, non la probabilità della prossima partita. I campioni delle squadre possono sovrapporsi e non vengono sommati.')
    if any(e['tipo'] == 'corners' for e in context):
        st.caption('La media corner non indica la frequenza di superamento di una soglia. Mancano campione, dispersione e separazione a favore/contro.')


def load(kind):
    try:
        result = api('contents/manual-' + kind + '.json?ref=' + BRANCH)
        data = json.loads(base64.b64decode(result['content']))
        if data.get('version') == 1 and data.get('tipo') == kind and isinstance(data.get('righe'), list):
            return data
    except (OSError, ValueError, KeyError, TypeError): pass
    return None


def save(data, token):
    path = 'contents/manual-' + data['tipo'] + '.json'
    body = dict(message='Aggiorna statistiche manuali ' + data['tipo'], branch=BRANCH,
                content=base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode())
    try: body['sha'] = api(path + '?ref=' + BRANCH, token)['sha']
    except HTTPError as error:
        if error.code != 404: raise
    api(path, token, body)


def render():
    import streamlit as st
    cached_load = st.cache_data(ttl=60)(load)
    with st.expander('Statistiche aggiuntive · aggiornamento manuale settimanale'):
        st.caption('Archivio condiviso: vengono salvate solo le tabelle, mai l’HTML originale. Nessun accesso automatico a FootyStats.')
        st.caption('Il generatore abbina queste statistiche alle squadre della giornata. Senza quote reali BTTS/corner non genera giocate per quei mercati. Le classifiche sono parziali.')
        for kind, label in [('btts', 'Entrambe segnano (BTTS)'), ('corners', 'Corner totali')]:
            st.subheader(label)
            data = st.session_state.get('manual_' + kind) or cached_load(kind)
            upload = st.file_uploader('Aggiorna ' + label, type=['html', 'htm'], key='html_' + kind)
            page_date = st.date_input('Data in cui hai salvato la pagina', max_value=datetime.now(timezone.utc).date(), key='page_date_' + kind)
            if st.button('Salva ' + label, key='save_html_' + kind):
                try:
                    if upload is None: raise ValueError('Seleziona prima un file HTML.')
                    data = parse(upload.getvalue(), kind)
                    data['pagina_salvata_il'] = page_date.isoformat()
                    st.session_state['manual_' + kind] = data
                    try: token = st.secrets.get('ATLAS_STORAGE_TOKEN', '')
                    except Exception: token = ''
                    if not token:
                        st.warning('Salvataggio online non configurato: dati disponibili solo in questa sessione.')
                    else:
                        try:
                            save(data, token)
                            cached_load.clear()
                            st.success('Tabella salvata online e disponibile anche alla riapertura.')
                        except Exception:
                            st.warning('Salvataggio online fallito: dati disponibili solo in questa sessione.')
                except ValueError as error: st.error(str(error))
            if data:
                st.caption('Caricamento: ' + data['caricato_il'] + ' · ' + str(len(data['righe'])) + ' squadre')
                if stale(data): st.warning('File caricato da almeno 7 giorni: aggiornare le statistiche.')
                st.dataframe(data['righe'], hide_index=True)
                st.caption('Data di caricamento, non data certificata delle statistiche. Stagione e competizione non certificate; oggi/domani non vengono conservati. Frequenze storiche, non probabilità garantite.')
            else: st.info('Nessuna tabella salvata.')
