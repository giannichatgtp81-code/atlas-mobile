"""Private capability-code storage. Server-only Supabase secret; no public cache."""
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class StorageError(Exception):
    pass


class ConflictError(StorageError):
    pass


def new_code():
    return 'atlas-' + secrets.token_urlsafe(24)


def code_id(code):
    code = code.strip()
    if not re.fullmatch(r'atlas-[A-Za-z0-9_-]{32}', code):
        raise StorageError('Codice non valido. Incolla il codice personale completo.')
    return hashlib.sha256(('atlas-mm-v1:' + code).encode()).hexdigest()


class Store:
    def __init__(self, url, key):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not re.fullmatch(r'[a-z0-9]+\.supabase\.co', parsed.netloc) or parsed.path not in ('', '/'):
            raise StorageError('Configurazione archivio non valida.')
        if not key.startswith('sb_secret_'):
            raise StorageError('Serve la chiave segreta server di Supabase.')
        self.url = url.rstrip('/') + '/rest/v1/atlas_mm_sequences'
        self.key = key

    def request(self, method, query=None, body=None):
        url = self.url + ('?' + urlencode(query) if query else '')
        headers = {'apikey': self.key, 'Content-Type': 'application/json',
                   'Prefer': 'return=representation', 'User-Agent': 'Atlas-MM'}
        request = Request(url, headers=headers, method=method,
                          data=json.dumps(body, allow_nan=False).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            # Never surface provider response bodies, headers, URLs or credentials.
            if error.code == 409:
                raise ConflictError('Sequenza già presente o aggiornata. Ricarica la sequenza.') from None
            raise StorageError('Archivio non disponibile. Nessuna modifica confermata: riprova.') from None
        except (URLError, OSError, ValueError):
            raise StorageError('Connessione all’archivio non riuscita. Riprova.') from None

    def create(self, state=None):
        code = new_code()
        rows = self.request('POST', body={'id': code_id(code), 'state': state, 'version': 0})
        if not isinstance(rows, list) or len(rows) != 1:
            raise StorageError('Creazione non confermata. Riprova.')
        return code, rows[0]

    def load(self, identifier):
        self.validate_id(identifier)
        rows = self.request('GET', {'id': 'eq.' + identifier, 'select': 'state,version', 'limit': '1'})
        if not rows:
            raise StorageError('Codice non trovato. Controlla di averlo copiato correttamente.')
        return rows[0]

    def save(self, identifier, state, version):
        self.validate_id(identifier)
        if type(version) is not int or version < 0:
            raise StorageError('Versione sequenza non valida.')
        rows = self.request('PATCH', {'id': 'eq.' + identifier, 'version': 'eq.' + str(version)},
                            {'state': state, 'version': version + 1,
                             'updated_at': datetime.now(timezone.utc).isoformat()})
        if not rows:
            raise ConflictError('La sequenza è cambiata su un altro dispositivo. Premi Ricarica sequenza prima di continuare.')
        return rows[0]

    @staticmethod
    def validate_id(identifier):
        if not re.fullmatch(r'[a-f0-9]{64}', identifier):
            raise StorageError('Accesso sequenza non valido.')


def gate(st):
    """None = offline fallback, False = cloud configured but not signed in."""
    try:
        url = st.secrets.get('SUPABASE_URL', '')
        key = st.secrets.get('SUPABASE_SECRET_KEY', '')
    except Exception:
        url = key = ''
    if not url and not key:
        return None
    try:
        store = Store(url, key)
    except StorageError as error:
        st.error(str(error))
        return False
    session = st.session_state.get('mm_cloud_session')
    if not session:
        st.caption('Ogni persona usa un codice diverso. Il codice dà accesso alla sequenza: non condividerlo.')
        with st.form('mm_cloud_login', clear_on_submit=True):
            code = st.text_input('Il tuo codice personale', type='password')
            login = st.form_submit_button('Riprendi la mia sequenza')
        if login:
            try:
                identifier = code_id(code)
                row = store.load(identifier)
                install(st, identifier, row)
                st.rerun()
            except StorageError as error:
                st.error(str(error))
        if st.button('Crea il mio codice personale'):
            try:
                code, row = store.create()
                install(st, code_id(code), row)
                st.session_state['mm_cloud_new_code'] = code
                st.rerun()
            except StorageError as error:
                st.error(str(error))
        return False
    new = st.session_state.get('mm_cloud_new_code')
    if new:
        st.warning('Conserva questo codice in un posto sicuro. Senza di esso non puoi recuperare la sequenza.')
        st.code(new, language=None)
        if st.button('Ho conservato il codice'):
            st.session_state.pop('mm_cloud_new_code', None)
            st.rerun()
        # Do not permit edits until the code has been acknowledged.
        return False
    st.caption('Sequenza personale online · ogni modifica viene salvata automaticamente.')
    left, right = st.columns(2)
    if left.button('Ricarica sequenza'):
        try:
            install(st, session['id'], store.load(session['id']))
            st.rerun()
        except StorageError as error:
            st.error(str(error))
    if right.button('Esci dal Money Management'):
        for name in list(st.session_state):
            if name.startswith(('mm_single', 'mm_cloud', 'mm_reset')):
                del st.session_state[name]
        st.rerun()
    return store


def install(st, identifier, row):
    from money_management_single import import_sequence
    raw = row.get('state')
    try:
        state = import_sequence(json.dumps(raw)) if raw is not None else None
        version = row['version']
        if type(version) is not int or version < 0:
            raise ValueError('Versione non valida.')
    except (KeyError, ValueError, TypeError):
        raise StorageError('Sequenza archiviata non valida. Contatta il gestore di Atlas.') from None
    # Clear user-specific widgets when switching sequence or refreshing from cloud.
    for name in list(st.session_state):
        if name.startswith(('mm_single', 'mm_reset')):
            del st.session_state[name]
    st.session_state['mm_cloud_session'] = {'id': identifier, 'version': version}
    if state is not None:
        st.session_state['mm_single'] = state


def commit(st, store, state):
    """Only replace local state after a confirmed database write."""
    from money_management_single import export_sequence
    if store is not None:
        session = st.session_state['mm_cloud_session']
        row = store.save(session['id'], json.loads(export_sequence(state)), session['version'])
        session['version'] = row['version']
    st.session_state['mm_single'] = state
    st.session_state['mm_single_revision'] = st.session_state.get('mm_single_revision', 0) + 1

