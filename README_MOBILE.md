# Atlas Mobile

Versione web di Atlas per caricare un palinsesto PDF Oddsprinter o CSV dal telefono e generare pronostici.

## Pubblicazione

1. In Streamlit Community Cloud, crea una nuova app dal repository.
2. Usa `atlas_mobile.py` come file principale.
3. Usa `requirements_mobile.txt` come dipendenze, rinominandolo in `requirements.txt` nel repository dedicato.

La versione mobile non richiede credenziali bookmaker e non conserva PDF, CSV o risultati dopo la sessione.

## Formato CSV

La colonna obbligatoria è `Partita`. Le colonne consigliate sono `Data`, `Ora`, `Manifestazione`, `1`, `X`, `2`, `Over 2.5`, `Under 2.5`, `Goal`, `No Goal`.
