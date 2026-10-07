from io import BytesIO
from pathlib import Path
import tempfile
import pandas as pd
import streamlit as st
from pdf_odds_importer import OddsPrinterPdfParser
from mobile_combo_engine import analyse_palinsesto, best_ticket

st.set_page_config(page_title='Atlas Mobile',page_icon='⚽',layout='centered')
st.markdown('''<style>
.stApp {background:#F2F8FD;color:#42576A;}
h1,h2,h3 {color:#173B59!important;}
div.stButton>button,div.stFormSubmitButton>button {background:#369DDB;color:white;border:0;border-radius:12px;}
div[data-testid="stForm"] {background:white;border:1px solid #CDE5F5;border-radius:16px;}
div[data-testid="stVerticalBlockBorderWrapper"]>div {border-color:#CDE5F5!important;}
</style>''',unsafe_allow_html=True)

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
        st.info('Nessuna proposta valida con le quote del palinsesto.')
        return
    with st.container(border=True):
        for leg in ticket['legs']:
            label = 'quota stimata' if leg.get('quota_stimata') else 'quota reale'
            st.write(f"**{leg['partita']}** — {leg['mercato']} · {label} {leg['quota']:.2f}")
        label = 'Quota totale stimata' if any(e.get('quota_stimata') for e in ticket['legs']) else 'Quota totale'
        st.write(f"**{label} {ticket['quota']:.2f}** · probabilità stimata {ticket['prob']*100:.1f}%")

st.title('Atlas Mobile')
st.caption('Singola del giorno · quota 2 · quota 3 · listone 30 combo')
with st.form('analysis'):
    upload = st.file_uploader('Carica il palinsesto PDF Oddsprinter o CSV',type=['pdf','csv'])
    submitted = st.form_submit_button('Genera pronostici',use_container_width=True)
if submitted:
    st.session_state.pop('palinsesto_analysis',None)
    if upload is None: st.error('Seleziona prima un file.')
    else:
        try:
            with st.spinner('Analisi del palinsesto…'):
                st.session_state['palinsesto_analysis'] = analyse_palinsesto(read_rows(upload))
                st.session_state['combo_version'] = st.session_state.get('combo_version',0)+1
        except Exception as error: st.error(f'Impossibile analizzare il file: {error}')

analysis = st.session_state.get('palinsesto_analysis')
if analysis is not None:
    events = analysis['listone']
    if not events and not analysis['daily']:
        st.info('Nessun evento utilizzabile: servono quote 1X2 e almeno un mercato completo Under/Over o Goal/No Goal.')
    else:
        selected = analysis['daily']
        st.caption(f"{analysis['analysed']} partite analizzate · proposte automatiche da tutto il palinsesto")
        single = max(selected,key=lambda e:e['prob']) if selected else None
        show('Singola del giorno',{'legs':[single],'quota':single['quota'],'prob':single['prob']} if single else None)
        show('Quota 2 · 2–3 eventi · fascia 1,90–2,10',best_ticket(selected,2,3))
        show('Quota 3 · 3–4 eventi · massimo 1,55 per evento',best_ticket(selected,3,4,max_leg_odds=1.55,min_events=3))
        with st.expander(f'Le migliori combo ({len(events)}/30)'):
            frame = pd.DataFrame([{'Partita':e['partita'],'Combo':e['mercato'],'Quota':round(e['quota'],2),'Fonte quota':'Stimata' if e.get('quota_stimata') else 'File','Probabilità stimata %':round(e['prob']*100,1)} for e in events])
            st.dataframe(frame,hide_index=True,use_container_width=True)
        if any(e.get('quota_stimata') for e in events):
            st.caption('Nel listone le quote combo mancanti sono teoriche (1/probabilità), non prezzi del bookmaker. Le tre proposte giornaliere usano soltanto quote presenti nel file.')
        st.caption('Probabilità stimate dalle quote con modello Poisson, non percentuali di successo verificate. Per le multiple il calcolo assume eventi indipendenti.')
