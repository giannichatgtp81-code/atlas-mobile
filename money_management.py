"""Port della matrice e della puntata differenziale lette da MM_Diff.exe."""
from math import isfinite

def matrix(odds, expected):
    n = len(odds)
    if not n or not 0 <= expected <= n or any(not isfinite(q) or q <= 1 for q in odds):
        raise ValueError('Quote o numero di risultati attesi non validi.')
    values = [[0.]*(expected+1) for _ in range(n+1)]
    values[n][expected] = 1.
    for i in range(n-1,-1,-1):
        for wins in range(expected,-1,-1):
            if wins == expected: values[i][wins] = 1.
            elif i > n-expected+wins: values[i][wins] = 0.
            elif i == n-expected+wins: values[i][wins] = odds[i]*values[i+1][wins+1]
            else:
                lose, win = values[i+1][wins], values[i+1][wins+1]
                denominator = lose+(odds[i]-1)*win
                values[i][wins] = odds[i]*lose*win/denominator if denominator else 0.
    return values

def stake(odds,expected,played,wins,cash):
    n = len(odds)
    if played >= n or wins >= expected or n-played < expected-wins or cash <= 0: return 0.
    if n-played == expected-wins: return cash
    values = matrix(odds,expected)
    win, lose = values[played+1][wins+1], values[played+1][wins]
    denominator = lose+(odds[played]-1)*win
    return cash*(1-odds[played]*win/denominator) if denominator else 0.

def initialise(cash,odds,expected_a,expected_b):
    if not isfinite(cash) or cash <= 0: raise ValueError('Cassa non valida.')
    opposite = [q/(q-1) for q in odds]
    a,b = matrix(odds,expected_a)[0][0],matrix(opposite,expected_b)[0][0]
    if a <= 0 or b <= 0: raise ValueError('Sequenza non valida.')
    return {'initial':cash,'cash_a':cash*b/(a+b),'cash_b':cash*a/(a+b),
            'odds':odds,'expected_a':expected_a,'expected_b':expected_b,
            'wins_a':0,'wins_b':0,'played':0,'history':[]}

def next_stakes(state):
    q = state['odds']; opposite = [o/(o-1) for o in q]; i = state['played']
    if i >= len(q): return (0.,0.,'Terminata',0.)
    a = stake(q,state['expected_a'],i,state['wins_a'],state['cash_a'])
    b = stake(opposite,state['expected_b'],i,state['wins_b'],state['cash_b'])
    difference = a-b*(opposite[i]-1)
    if abs(difference)<1e-8: return (a,b,'Nessuna',0.)
    return (a,b,'A',difference) if difference>0 else (a,b,'B',b-a*(q[i]-1))

def record(state,won):
    a,b,side,amount = next_stakes(state)
    i = state['played']
    if i >= len(state['odds']): raise ValueError('Sequenza terminata.')
    q = state['odds'][i]; qb=q/(q-1)
    state['cash_a'] += a*(q-1) if won else -a
    state['cash_b'] += -b if won else b*(qb-1)
    state['wins_a'] += int(won); state['wins_b'] += int(not won); state['played'] += 1
    state['history'].append({'Evento':i+1,'Quota A':q,'Esito A':'Vinta' if won else 'Persa','Lato differenziale':side,'Importo':round(amount,2),'Cassa totale':round(state['cash_a']+state['cash_b'],2)})

def render():
    import streamlit as st
    import pandas as pd
    import json
    with st.expander('Money management · differenziale'):
        st.caption('Matrice A/B del programma fornito. Calcolatore manuale: nessuna puntata viene inviata. La quota B è la complementare teorica, non una quota bookmaker.')
        with st.form('mm_init'):
            cash = st.number_input('Cassa iniziale (€)',min_value=1.,value=100.,step=10.)
            total = st.number_input('Eventi nella sequenza',min_value=2,max_value=100,value=10,step=1)
            expected_a = st.number_input('Vinti attesi A',min_value=1,max_value=100,value=6,step=1)
            expected_b = st.number_input('Vinti attesi B',min_value=1,max_value=100,value=4,step=1)
            odd = st.number_input('Quota A iniziale',min_value=1.01,value=2.,step=.01)
            if st.form_submit_button('Avvia nuova sequenza'):
                try: st.session_state['mm_state'] = initialise(cash,[odd]*int(total),int(expected_a),int(expected_b))
                except ValueError as e: st.error(str(e))
        state = st.session_state.get('mm_state')
        if state:
            st.write(f"Evento {min(state['played']+1,len(state['odds']))}/{len(state['odds'])} · cassa totale € {state['cash_a']+state['cash_b']:.2f}")
            if state['played'] < len(state['odds']):
                with st.form('mm_update'):
                    current = st.number_input('Quota A del prossimo evento',min_value=1.01,value=float(state['odds'][state['played']]),step=.01)
                    if st.form_submit_button('Ricalcola'):
                        state['odds'][state['played']] = current
                a,b,side,amount = next_stakes(state)
                st.write(f"Puntata A € {a:.2f} · puntata B € {b:.2f}")
                st.write(f"**Differenziale: lato {side} · € {amount:.2f}**")
                st.caption('Il procedimento può arrivare a impegnare l’intera cassa; il risultato dipende dagli esiti attesi impostati.')
                c1,c2 = st.columns(2)
                if c1.button('A vinta',key='mm_win'): record(state,True); st.rerun()
                if c2.button('A persa',key='mm_loss'): record(state,False); st.rerun()
            else: st.info('Sequenza terminata.')
            if state['history']: st.dataframe(pd.DataFrame(state['history']),hide_index=True,use_container_width=True)
            st.download_button('Salva sequenza',json.dumps(state,ensure_ascii=False,indent=2),'atlas_money_management.json','application/json')
            st.caption('I dati restano nella sessione: salva la sequenza prima di chiudere la pagina. Questo port include il differenziale a sequenza singola; trading e sequenze multiple del programma non sono ancora inclusi.')
