from io import BytesIO
from pathlib import Path
import tempfile
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pdf_odds_importer import OddsPrinterPdfParser
from mobile_combo_engine import analyse_palinsesto, best_ticket
from football_data_client import Client as FootballClient, enrich as enrich_football
from money_management_single import render as render_money_management
from analysis_storage import load as load_analysis, save as save_analysis, payload, active

st.set_page_config(page_title='Atlas Mobile',page_icon='⚽',layout='centered')
st.markdown('''<style>
.stApp {background:#F2F8FD;color:#173B59;color-scheme:light;}
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stWidgetLabel"],
.stApp [data-testid="stCaptionContainer"],
.stApp label {color:#173B59!important;}
h1,h2,h3 {color:#173B59!important;}
div.stButton>button,div.stFormSubmitButton>button {background:#167AB5;color:white!important;border:0;border-radius:12px;}
.stApp button [data-testid="stMarkdownContainer"] {color:inherit!important;}
.stApp input,.stApp textarea {background:#FFFFFF!important;color:#173B59!important;-webkit-text-fill-color:#173B59!important;caret-color:#173B59;}
.stApp input::placeholder,.stApp textarea::placeholder {color:#536D82!important;-webkit-text-fill-color:#536D82!important;opacity:1;}
.stApp [data-baseweb="input"],.stApp [data-baseweb="base-input"],
.stApp [data-baseweb="textarea"],.stApp [data-baseweb="select"]>div {background:#FFFFFF!important;color:#173B59!important;border-color:#A8CDE5!important;}
.stApp [data-testid="stNumberInput"] button {background:#E2F1FB!important;color:#173B59!important;}
.stApp [data-testid="stExpander"] details,
.stApp [data-testid="stExpander"] summary {background:#FFFFFF!important;color:#173B59!important;border-color:#CDE5F5!important;}
.stApp [data-testid="stFileUploaderDropzone"] {background:#FFFFFF!important;color:#173B59!important;border:1px solid #A8CDE5!important;}
.stApp [data-testid="stFileUploaderDropzone"] button,
.stApp [data-testid="stDownloadButton"] button {background:#E2F1FB!important;color:#173B59!important;border:1px solid #A8CDE5!important;}
.stApp [data-testid="stRadio"] [role="radiogroup"] {color:#173B59!important;}
.stApp [data-testid="stHeader"] {background:#F2F8FD!important;}
.stApp [data-testid="stCode"],.stApp pre {background:#E2F1FB!important;color:#173B59!important;}
.stApp button:focus-visible,.stApp input:focus-visible {outline:2px solid #167AB5!important;outline-offset:2px;}
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
    return [{'Partita':e.match_name,'Data':e.date,'Ora':e.time,'Competizione':e.competition,**e.odds} for e in events]

def show(title,ticket):
    st.subheader(title)
    if ticket is None:
        st.info('Nessuna proposta valida con le quote del palinsesto.')
        return
    with st.container(border=True):
        for leg in ticket['legs']:
            label = 'quota stimata' if leg.get('quota_stimata') else 'quota reale'
            st.write(f"**{leg['partita']}** — {leg['mercato']} · {label} {leg['quota']:.2f}")
            st.caption(leg.get('fonte_analisi', 'Solo quote'))
        label = 'Quota totale stimata' if any(e.get('quota_stimata') for e in ticket['legs']) else 'Quota totale'
        st.write(f"**{label} {ticket['quota']:.2f}** · probabilità stimata {ticket['prob']*100:.1f}%")

st.title('Atlas Mobile')
st.caption('Singola del giorno · quota 2 · quota 3 · listone 30 combo')
@st.cache_data(ttl=60)
def public_analysis():
    return load_analysis()

@st.cache_resource
def football_client():
    return FootballClient()

if 'palinsesto_analysis' not in st.session_state:
    saved_analysis=public_analysis()
    if saved_analysis and active(saved_analysis):
        st.session_state['palinsesto_analysis']=saved_analysis['analysis']
        st.session_state['analysis_saved']=saved_analysis
with st.form('analysis'):
    upload = st.file_uploader('Carica il palinsesto PDF Oddsprinter o CSV',type=['pdf','csv'])
    submitted = st.form_submit_button('Genera pronostici',use_container_width=True)
if submitted:
    st.session_state.pop('palinsesto_analysis',None)
    if upload is None: st.error('Seleziona prima un file.')
    else:
        try:
            with st.spinner('Analisi del palinsesto…'):
                rows = read_rows(upload)
                rows, pitch_report = enrich_football(rows, football_client())
                st.session_state['palinsesto_analysis'] = analyse_palinsesto(rows)
                st.session_state['palinsesto_analysis']['pitchapi'] = pitch_report
                daily_payload=payload(st.session_state['palinsesto_analysis'])
                st.session_state['analysis_saved']=daily_payload
                try: token=st.secrets.get('ATLAS_STORAGE_TOKEN','')
                except Exception:token=''
                if token:
                    try:
                        save_analysis(daily_payload,token)
                        public_analysis.clear()
                        st.success('Analisi salvata online per la giornata di riferimento.')
                    except Exception:
                        st.warning('Analisi completata, ma il salvataggio online non è riuscito. Il risultato è disponibile in questa sessione.')
                else:
                    st.warning('Archivio giornaliero da attivare: questa nuova analisi resta per ora nella sessione corrente.')
                st.session_state['combo_version'] = st.session_state.get('combo_version',0)+1
        except Exception as error: st.error(f'Impossibile analizzare il file: {error}')

stored=st.session_state.get('analysis_saved')
if stored and not active(stored):
    st.session_state.pop('palinsesto_analysis',None)
    st.session_state.pop('analysis_saved',None)
analysis = st.session_state.get('palinsesto_analysis')
if analysis is not None:
    report = analysis.get('pitchapi')
    if report:
        provider = report.get('provider', 'PitchAPI (analisi precedente: rigenerare)')
        st.caption(f"{provider}: storico usato per {report['enriched']}/{report['total']} partite. Le altre restano basate sulle quote.")
        st.caption(f"Risultati storici ricevuti: {report.get('fixtures_received', '—')} · partite con squadre riconosciute: {report.get('matched', 0)} · storico insufficiente: {report.get('insufficient_history', '—')}")
        if report.get('unsupported'):
            st.caption(f"Partite senza competizione supportata o dati identificativi: {report['unsupported']}.")
        if report.get('latest_dates'):
            st.caption('Ultimi risultati disponibili per archivio: ' + ' · '.join(f'{code}: {date}' for code, date in sorted(report['latest_dates'].items())))
        for message in report.get('errors', []): st.warning(message)
        st.caption(f'Dati: {provider} · modello sperimentale, miglioramento non ancora validato.')
    if stored:st.caption(f"Palinsesto del {stored['reference_date']} · valido fino alla mezzanotte della giornata indicata")
    events = analysis['listone']
    if not events and not analysis['daily']:
        st.info('Nessun evento utilizzabile: servono quote 1X2 e almeno un mercato completo Under/Over o Goal/No Goal.')
    else:
        selected = analysis['daily']
        st.caption(f"{analysis['analysed']} partite analizzate · proposte automatiche da tutto il palinsesto")
        single_options = [e for e in selected if e['quota'] >= 1.40]
        single = max(single_options,key=lambda e:e['prob']) if single_options else None
        show('Singola del giorno · quota minima 1,40',{'legs':[single],'quota':single['quota'],'prob':single['prob']} if single else None)
        show('Quota 2 · 2–3 eventi · fascia 1,90–2,10',best_ticket(selected,2,3))
        show('Quota 3 · 3–4 eventi · massimo 1,55 per evento',best_ticket(selected,3,4,max_leg_odds=1.55,min_events=3))
        with st.expander(f'Le migliori combo ({len(events)}/30)'):
            frame = pd.DataFrame([{'Partita':e['partita'],'Combo':e['mercato'],'Quota':round(e['quota'],2),'Fonte quota':'Stimata' if e.get('quota_stimata') else 'File','Probabilità stimata %':round(e['prob']*100,1)} for e in events])
            st.dataframe(frame,hide_index=True,use_container_width=True)
        if any(e.get('quota_stimata') for e in events):
            st.caption('Nel listone le quote combo mancanti sono teoriche (1/probabilità), non prezzi del bookmaker. Le tre proposte giornaliere usano soltanto quote presenti nel file.')
        st.caption('Probabilità stimate con modello Poisson dalle quote e, dove disponibile, dallo storico sportivo. Non sono percentuali di successo verificate. Per le multiple il calcolo assume eventi indipendenti.')

with st.expander('Livescore · SoccersAPI'):
    st.caption('Risultati e statistiche forniti da SoccersAPI. Questa sezione non alimenta automaticamente i pronostici di Atlas.')
    if st.checkbox('Mostra il livescore esterno', key='show_soccersapi_livescore'):
        components.iframe(
            'https://embed.soccersapi.com/widgets/ls-soccersapi/free.html?uid=6ac91d94e239292ca271033d&widget-id=livescore&locale=es',
            height=1200, scrolling=True,
        )

render_money_management()
