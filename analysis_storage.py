"""Archivio pubblico giornaliero su ramo GitHub separato dal codice."""
import base64
import json
from datetime import datetime,timedelta
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

REPO='giannichatgtp81-code/atlas-mobile'
BRANCH='atlas-data'
FILE='daily-analysis.json'
TIMEZONE=ZoneInfo('Europe/Rome')

def payload(analysis,now=None):
    now=now or datetime.now(TIMEZONE)
    dates=set()
    for e in analysis['daily']+analysis['listone']:
        try: dates.add(datetime.strptime(e['data'],'%d/%m/%Y').date())
        except (ValueError,TypeError):pass
    future=sorted(d for d in dates if d>=now.date())
    reference=future[0] if future else (max(dates) if dates else now.date())
    expiry=datetime.combine(reference+timedelta(days=1),datetime.min.time(),tzinfo=TIMEZONE)
    return {'version':1,'reference_date':reference.isoformat(),'expires_at':expiry.isoformat(),'analysis':analysis}

def active(data,now=None):
    try:
        expiry=datetime.fromisoformat(data['expires_at'])
        return data.get('version')==1 and expiry.tzinfo is not None and (now or datetime.now(TIMEZONE))<expiry and isinstance(data.get('analysis'),dict)
    except (KeyError,ValueError,TypeError):return False

def api(path,token=None,body=None):
    headers={'Accept':'application/vnd.github+json','User-Agent':'Atlas-Mobile'}
    if token:headers['Authorization']='Bearer '+token
    content=json.dumps(body).encode() if body is not None else None
    request=Request('https://api.github.com/repos/'+REPO+'/'+path,data=content,headers=headers,method='PUT' if body is not None and path.startswith('contents/') else ('POST' if body is not None else 'GET'))
    with urlopen(request,timeout=15) as response:return json.load(response)

def load():
    try:
        request=Request('https://raw.githubusercontent.com/'+REPO+'/'+BRANCH+'/'+FILE,headers={'User-Agent':'Atlas-Mobile'})
        with urlopen(request,timeout=15) as response:data=json.load(response)
        return data if active(data) else None
    except (HTTPError,OSError,ValueError,KeyError,TypeError):return None

def save(data,token):
    try:api('git/ref/heads/'+BRANCH,token)
    except HTTPError as error:
        if error.code!=404:raise
        main=api('git/ref/heads/main',token)
        api('git/refs',token,{'ref':'refs/heads/'+BRANCH,'sha':main['object']['sha']})
    body={'message':'Salva analisi pubblica '+data['reference_date'],'branch':BRANCH,
          'content':base64.b64encode(json.dumps(data,ensure_ascii=False).encode()).decode()}
    try:body['sha']=api('contents/'+FILE+'?ref='+BRANCH,token)['sha']
    except HTTPError as error:
        if error.code!=404:raise
    api('contents/'+FILE,token,body)
