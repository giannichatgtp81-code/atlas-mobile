"""Money management a sequenza singola, basato sulla matrice MM originale."""
from math import isfinite
import json
from money_management import matrix, stake

def initialise(cash, odds, expected):
    if not isfinite(cash) or cash <= 0 or not 1 <= expected <= len(odds):
        raise ValueError('Cassa positiva e Attesi compresi tra 1 ed Eventi.')
    matrix(odds,expected)
    return {'initial':cash,'cash':cash,'odds':list(odds),'expected':expected,'history':[]}

def status(state):
    played=len(state['history']); wins=sum(e['won'] for e in state['history'])
    remaining=len(state['odds'])-played
    terminal = played>=len(state['odds']) or wins>=state['expected'] or remaining<state['expected']-wins or state['cash']<=0
    amount=0. if terminal else stake(state['odds'],state['expected'],played,wins,state['cash'])
    if terminal:
        final=state['cash']
    else:
        final=state['cash']*matrix(state['odds'],state['expected'])[played][wins]
    return {'played':played,'wins':wins,'losses':played-wins,'terminal':terminal,'stake':amount,'final_if_target':final}

def record(state,won):
    info=status(state)
    if info['terminal']:raise ValueError('Sequenza conclusa.')
    q=state['odds'][info['played']]; amount=info['stake']
    previous=state['cash']
    state['cash']=previous+amount*(q-1) if won else previous-amount
    state['history'].append({'won':bool(won),'quota':q,'stake':amount,'cash_before':previous,'cash_after':state['cash']})

def undo(state):
    if state['history']: state['cash']=state['history'].pop()['cash_before']

def parse_odds(text,total):
    values=[float(v.strip().replace(',','.')) for v in text.replace('\n',';').split(';') if v.strip()]
    if len(values)!=total:raise ValueError(f'Inserisci {total} quote, separate da punto e virgola.')
    return values

def export_sequence(state):
    return json.dumps({'format':'atlas-mm-single','version':1,'state':state},ensure_ascii=False,indent=2).encode('utf-8')

def import_sequence(content):
    try:
        data=json.loads(content)
        if data.get('format')!='atlas-mm-single' or data.get('version')!=1:raise ValueError('Formato non riconosciuto.')
        raw=data['state'];odds=raw['odds'];expected=raw['expected']
        if not isinstance(odds,list) or not 2<=len(odds)<=100 or type(expected) is not int:raise ValueError('Sequenza non valida.')
        state=initialise(float(raw['initial']),[float(q) for q in odds],expected)
        history=raw['history']
        if not isinstance(history,list) or len(history)>len(odds):raise ValueError('Conteggi non validi.')
        # Ricostruisce la cassa dai movimenti, senza fidarsi del saldo nel file.
        for index,event in enumerate(history):
            if type(event['won']) is not bool:raise ValueError('Esito non valido.')
            amount=float(event['stake']);quota=float(event['quota'])
            if not isfinite(amount) or not 0<=amount<=state['cash']+1e-7 or quota!=state['odds'][index]:raise ValueError('Movimento non valido.')
            before=state['cash'];state['cash']=before+amount*(quota-1) if event['won'] else before-amount
            state['history'].append({'won':event['won'],'quota':quota,'stake':amount,'cash_before':before,'cash_after':state['cash']})
        return state
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError) as error:
        raise ValueError('File sequenza non valido: '+str(error)) from error

def render():
    import streamlit as st
    import pandas as pd
    with st.expander('Money Management'):
        saved=st.file_uploader('Riprendi una sequenza salvata',type=['json'],key='mm_single_restore')
        if saved is not None and st.button('Ripristina sequenza'):
            try:
                st.session_state['mm_single']=import_sequence(saved.getvalue())
                st.session_state['mm_single_revision']=st.session_state.get('mm_single_revision',0)+1
                st.success('Sequenza ripristinata con tutti gli esiti e la cassa aggiornata.')
            except ValueError as error:st.error(str(error))
        st.subheader('Impostazioni iniziali')
        mode=st.radio('Quote iniziali',('Uguali','Diverse'),horizontal=True,key='mm_single_mode')
        with st.form('mm_single_init'):
            cash=st.number_input('Cassa (€)',min_value=1.,value=100.,step=10.)
            total=st.number_input('Eventi',min_value=2,max_value=100,value=10,step=1)
            expected=st.number_input('Attesi',min_value=1,max_value=100,value=6,step=1)
            if mode=='Uguali':
                quota=st.number_input('Quota iniziale',min_value=1.01,value=2.,step=.01)
            else:
                text=st.text_area('Quote degli eventi, separate da ;',placeholder='1,40; 1,50; 1,45; …')
            start=st.form_submit_button('Avvia',use_container_width=True)
        if start:
            try:
                odds=[quota]*int(total) if mode=='Uguali' else parse_odds(text,int(total))
                st.session_state['mm_single']=initialise(cash,odds,int(expected))
                st.session_state['mm_single_revision']=st.session_state.get('mm_single_revision',0)+1
            except (ValueError,OverflowError) as error:st.error(str(error))
        state=st.session_state.get('mm_single')
        if not state:return
        info=status(state)
        st.subheader('Azioni')
        st.write(f"**Cassa € {state['cash']:.2f}** · Eventi {info['played']}/{len(state['odds'])} · Attesi {state['expected']}")
        st.write(f"Vinti: {info['wins']} · Persi: {info['losses']}")
        st.write(f"Resa totale condizionata agli esiti attesi: € {info['final_if_target']:.2f}")
        if info['terminal']:
            st.info('Obiettivo raggiunto.' if info['wins']>=state['expected'] else 'Sequenza conclusa: obiettivo non raggiunto.')
        else:
            with st.form('mm_single_calc'):
                q=st.number_input('Quota del prossimo evento',min_value=1.01,value=float(state['odds'][info['played']]),step=.01,key=f"mm_single_q_{info['played']}_{st.session_state.get('mm_single_revision',0)}")
                new_expected=st.number_input('Attesi da raggiungere',min_value=1,max_value=len(state['odds']),value=state['expected'])
                if st.form_submit_button('Calcola'):
                    state['odds'][info['played']]=q;state['expected']=int(new_expected)
                    st.rerun()
            info=status(state)
            st.write(f"**Puntata calcolata: € {info['stake']:.2f}**")
            c1,c2=st.columns(2)
            if c1.button('Vinta',key='mm_single_win',disabled=info['terminal']):record(state,True);st.rerun()
            if c2.button('Persa',key='mm_single_loss',disabled=info['terminal']):record(state,False);st.rerun()
        c1,c2=st.columns(2)
        if c1.button('Correggi ultimo esito',disabled=not state['history']):
            undo(state);st.session_state['mm_single_revision']=st.session_state.get('mm_single_revision',0)+1;st.rerun()
        if c2.button('Reset sequenza'):
            st.session_state['mm_single']=initialise(state['initial'],state['odds'],state['expected']);st.rerun()
        st.download_button('Salva sequenza aggiornata',export_sequence(state),'atlas_sequenza.json','application/json',use_container_width=True)
        table=pd.DataFrame([{'Evento':i+1,'Quota':e['quota'],'Puntata €':round(e['stake'],2),'Esito':'Vinta' if e['won'] else 'Persa','Cassa €':round(e['cash_after'],2)} for i,e in enumerate(state['history'])])
        if not table.empty:
            st.dataframe(table,hide_index=True,use_container_width=True)
            st.download_button('Salva conteggi CSV',table.to_csv(index=False).encode('utf-8-sig'),'atlas_conteggi.csv','text/csv')
        st.caption('Il file di salvataggio si aggiorna a ogni esito: scaricalo per riprendere la sequenza anche dopo aver chiuso la pagina. La resa dipende dagli esiti attesi; il metodo può impegnare tutta la cassa.')
