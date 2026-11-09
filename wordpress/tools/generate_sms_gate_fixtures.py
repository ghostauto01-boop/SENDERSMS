#!/usr/bin/env python3
"""Selected actual provider AST, fake httpx only. Never imports app/config or sends HTTP/SMS."""
import ast,asyncio,base64,json,logging,types
from pathlib import Path
D=Path(__file__).resolve().parents[1]
path=D.parent/'backend/app/providers/smsgate.py'
ns=dict(base64=base64,json=json,logging=logging,logger=logging.getLogger('offline-smsgate'),Optional=__import__('typing').Optional,DEFAULT_BASE='https://api.sms-gate.app/3rdparty/v1',uuid=types.SimpleNamespace(uuid4=lambda:'synthetic-missing-provider-id'),settings=types.SimpleNamespace(SMSGATE_USERNAME='synthetic-user',SMSGATE_PASSWORD='synthetic-pass',SMSGATE_BASE_URL='https://api.sms-gate.app/3rdparty/v1'))
class TimeoutError(Exception):pass
requests=[];scenario={}
class Response:
 def __init__(self):self.status_code=scenario['http'];self.text=scenario.get('raw',json.dumps(scenario.get('json',{})))
 def json(self):return json.loads(self.text)
class Client:
 def __init__(self,**kwargs):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 async def post(self,url,**kwargs):
  requests.append(dict(method='POST',url=url,**kwargs))
  if scenario.get('timeout'):raise TimeoutError()
  return Response()
 async def get(self,url,**kwargs):
  requests.append(dict(method='GET',url=url,**kwargs));return Response()
ns['httpx']=types.SimpleNamespace(AsyncClient=Client,Timeout=lambda *a,**k:None,TimeoutException=TimeoutError)
names={'_base','_api','_creds','send_sms_with_credentials','test_connection_with_credentials','poll_status_for_ids'}
nodes=[n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'actual-source-smsgate','exec'),ns)
async def main():
 global scenario
 cases=[]
 for scenario in [dict(http=201,json={'id':'provider-1','state':'Pending'}),dict(http=200,json={'messageId':'provider-2','state':'Sent'}),dict(http=202,json={'id':'provider-3','state':'Delivered'}),dict(http=200,json={}),dict(http=302,json={'id':'redirect-not-trusted'}),dict(http=401,json={'message':'synthetic-private-error'}),dict(http=429,json={'error':'synthetic-private-error'}),dict(http=500,json={'id':'maybe-accepted'}),dict(http=200,raw='not-json'),dict(http=0,timeout=True)]:
  requests.clear();out=await ns['send_sms_with_credentials']('+2348031234567','Hello Ada',base_url=ns['DEFAULT_BASE'],username='synthetic-user',password='synthetic-pass',sim=2,device_id='device-1');cases.append(dict(response=scenario,source_expected=out,source_request=requests.copy()))
 polls=[]
 for state in ('Pending','Processing','Sent','Delivered','Failed','Cancelled','Undelivered','unknown'):
  scenario=dict(http=200,json={'id':'provider-1','state':state});requests.clear();out=await ns['poll_status_for_ids'](['provider-1']);polls.append(dict(response=scenario,source_expected=out,source_request=requests.copy()))
 scenario=dict(http=400,json={});requests.clear();probe=await ns['test_connection_with_credentials'](base_url=ns['DEFAULT_BASE'],username='synthetic-user',password='synthetic-pass');
 (D/'tests/sms-gate-source-fixtures.json').write_text(json.dumps(dict(cases=cases,polls=polls,source_probe=dict(result=probe,requests=requests),scope='Actual selected SMS-Gate provider AST with fake httpx, synthetic credentials and fixed UUID. No application imports or HTTP/SMS. Native deliberately never invents IDs, trusts redirects/5xx, calls undelivered delivered, or sends a diagnostic SMS.'),indent=2)+'\n')
asyncio.run(main())
