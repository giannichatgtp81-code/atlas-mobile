from io import BytesIO
from pathlib import Path
import tempfile
import pandas as pd
import streamlit as st
from pdf_odds_importer import OddsPrinterPdfParser
from mobile_combo_engine import candidates, best_ticket, number

st.set_page_config(page_title='Atlas Mobile',page_icon='⚽',layout='centered')

def read_rows(upload):
    if upload.name.lower().endswith('.csv'):
        frame = pd.read_csv(BytesIO(upload.getvalue()),sep=None,engine='python')
        if 'Partita' not in frame: raise ValueError('Il CSV deve contenere la colonna Partita.')
        return frame.where(pd.notnull(frame),None).to_dict('records')
    with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as file:
        file.write(upload.getvalue())
        path = Path(file.name)
    try: events = OddsPrinterPdfParser().parse(path)
    finally: path.unlink(missing_ok=True)
    return [{'Partita':e.match_name,'Data':e.date,'Ora':e.time,**e.odds} for e in events]

def show(title,ticket):
    st.subheader(title)
    if ticket is None:
        st.info('Nessuna proposta valida con gli eventi e le quote selezionati.')
        return
    for leg in ticket['legs']:
        st.write(f"**{leg['partita']}** — {leg['mercato']} · quota {leg['quota']:.2f}")
    st.write(f"**Quota totale {ticket['quota']:.2f}** · probabilità stimata {ticket['prob']*100:.1f}%")

st.title('Atlas Mobile')
st.caption('Combo sopra quota 1,20 · massimo 30 eventi · tre proposte')
with st.form('analysis'):
    upload = st.file_uploader('Carica il palinsesto PDF Oddsprinter o CSV',type=['pdf','csv'])
    submitted = st.form_submit_button('Genera pronostici',use_container_width=True)
if submitted:
    st.session_state.pop('combo_events',None)
    if upload is None: st.error('Seleziona prima un file.')
    else:
        try:
            with st.spinner('Analisi del palinsesto…'):
                st.session_state['combo_events'] = candidates(read_rows(upload))
                st.session_state['combo_version'] = st.session_state.get('combo_version',0)+1
        except Exception as error: st.error(f'Impossibile analizzare il file: {error}')

events = st.session_state.get('combo_events')
if events is not None:
    if not events:
        st.info('Nessun evento utilizzabile: servono quote 1X2 e almeno un mercato completo Under/Over o Goal/No Goal.')
    else:
        st.subheader(f'Eventi selezionabili ({len(events)}/30)')
        st.caption('Se la quota combo manca nel file, inserisci quella reale del bookmaker. Quote ≤ 1,20 escluse.')
        frame = pd.DataFrame([{'Seleziona':bool(e['quota']),'Partita':e['partita'],'Combo':e['mercato'],'Quota reale':e['quota'],'Probabilità stimata %':round(e['prob']*100,1)} for e in events])
        edited = st.data_editor(frame,hide_index=True,use_container_width=True,
            disabled=['Partita','Combo','Probabilità stimata %'],
            column_config={'Quota reale':st.column_config.NumberColumn(min_value=1.0,step=0.01,format='%.2f')},
            key=f"selection_{st.session_state['combo_version']}")
        selected = []
        for event,(_,record) in zip(events,edited.iterrows()):
            odd = number(record['Quota reale'])
            if record['Seleziona'] and odd and odd>1.20:
                selected.append({**event,'quota':odd})
        single = max(selected,key=lambda e:e['prob']) if selected else None
        show('Singola del giorno',{'legs':[single],'quota':single['quota'],'prob':single['prob']} if single else None)
        show('Quota 2 · 2–3 eventi · fascia 1,90–2,10',best_ticket(selected,2,3))
        show('Quota 3 · 2–4 eventi · fascia 2,85–3,15',best_ticket(selected,3,4))
        st.caption('Probabilità stimate dalle quote con modello Poisson, non percentuali di successo verificate. Per le multiple il calcolo assume eventi indipendenti.')
