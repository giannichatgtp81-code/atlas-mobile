"""Atlas Mobile: analisi di palinsesti dal browser, senza credenziali locali."""
from __future__ import annotations

from pathlib import Path
import tempfile

import pandas as pd
import streamlit as st

from mobile_value_engine import analyse_rows
from pdf_odds_importer import OddsPrinterPdfParser


st.set_page_config(page_title="Atlas Mobile", page_icon="⚽", layout="wide")


def _number(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
        return number if number > 1 else None
    except (TypeError, ValueError):
        return None


def rows_from_pdf(content: bytes) -> list[dict[str, object]]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(content)
        path = Path(handle.name)
    try:
        events = OddsPrinterPdfParser().parse(path)
    finally:
        path.unlink(missing_ok=True)
    return [
        {
            "ID Evento": event.source_code,
            "Data": event.date,
            "Ora": event.time,
            "Manifestazione": event.competition,
            "Partita": event.match_name,
            **event.odds,
        }
        for event in events
    ]


def rows_from_csv(content: bytes) -> list[dict[str, object]]:
    frame = pd.read_csv(__import__("io").BytesIO(content), sep=None, engine="python")
    required = {"Partita"}
    if not required.issubset(frame.columns):
        raise ValueError("Il CSV deve avere almeno la colonna ‘Partita’.")
    for column in frame.columns:
        if column not in {"Partita", "Data", "Ora", "Manifestazione", "ID Evento"}:
            frame[column] = frame[column].map(_number)
    return frame.where(pd.notnull(frame), None).to_dict("records")


def analyse(rows: list[dict[str, object]], only_value: bool) -> pd.DataFrame:
    result = analyse_rows(rows, only_value=only_value)
    frame = pd.DataFrame(result)
    if frame.empty:
        return frame
    frame["esito"] = frame["is_value"].map({True: "VALUE", False: "Studio"})
    return frame.rename(columns={
        "data": "Data", "ora": "Ora", "partita": "Partita", "comp": "Competizione",
        "mercato": "Pronostico", "quota": "Quota", "prob": "Probabilità %", "ev": "EV %", "esito": "Esito",
    })


st.title("Atlas Mobile")
st.caption("Carica quote dal telefono e genera pronostici. Le analisi restano disponibili solo durante questa sessione.")

with st.form("analysis"):
    upload = st.file_uploader("Palinsesto PDF Oddsprinter o CSV", type=["pdf", "csv"])
    only_value = st.toggle("Mostra solo VALUE", value=False)
    submitted = st.form_submit_button("Genera pronostici", use_container_width=True)

if submitted:
    if upload is None:
        st.error("Seleziona prima un file PDF o CSV.")
    else:
        try:
            content = upload.getvalue()
            rows = rows_from_pdf(content) if upload.name.lower().endswith(".pdf") else rows_from_csv(content)
            if not rows:
                raise ValueError("Non ho trovato partite utilizzabili nel file.")
            with st.spinner("Analisi in corso…"):
                st.session_state["mobile_result"] = analyse(rows, only_value)
                st.session_state["mobile_count"] = len(rows)
        except Exception as error:
            st.error(f"Impossibile analizzare il file: {error}")

result = st.session_state.get("mobile_result")
if isinstance(result, pd.DataFrame):
    st.subheader(f"Pronostici — {len(result)} risultati da {st.session_state.get('mobile_count', 0)} partite")
    if result.empty:
        st.info("Nessun pronostico soddisfa i filtri scelti.")
    else:
        columns = [name for name in ("Data", "Ora", "Partita", "Competizione", "Pronostico", "Quota", "Probabilità %", "EV %", "Esito") if name in result]
        st.dataframe(result[columns], use_container_width=True, hide_index=True, height=min(650, 70 + len(result) * 40))
        st.download_button(
            "Scarica risultati CSV",
            data=result[columns].to_csv(index=False).encode("utf-8-sig"),
            file_name="atlas_pronostici.csv",
            mime="text/csv",
            use_container_width=True,
        )

st.divider()
st.caption("Le quote stimate non sono quote di bookmaker. Verifica sempre la quota reale prima di giocare.")
