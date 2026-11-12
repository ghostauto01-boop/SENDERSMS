#!/usr/bin/env python3
"""Selected original URL DTO and endpoint, with DNS/httpx forcibly fake; no network or app imports."""
import asyncio,base64,importlib.util,json,socket,sys,types
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('offline_jobs_reference',D/'tools/generate_import_job_fixtures.py');jobs=importlib.util.module_from_spec(spec);spec.loader.exec_module(jobs);cg=jobs.cg;ns=jobs.ns
cg.load('backend/app/schemas/contact.py',{'ContactImportURLRequest'},cg.dto_ns);ns['ContactImportURLRequest']=cg.dto_ns['ContactImportURLRequest']
cg.load('backend/app/api/v1/contacts.py',{'create_csv_import_job_from_url'})
cases=[]
inputs=[{},None,[],{'url':'short'},{'url':None},{'url':'https://csv.example.net/list.csv','extra':1}]
for extra in [{'tags':[' Buyer ','buyer','VIP']},{'tags':'Buyer, buyer, VIP'},{'tags':'Buyer;VIP'},{'tags':None},{'tags':2},{'tags':[False]},{'tags':['x'*101]},{'column_mapping':[]},{'column_mapping':{'Email':False}},{'column_mapping':None},{'auto_tag_rules':None},{'auto_tag_rules':[False]},{'auto_tag_rules':[{'field':'Mood','contains':'warm','tag':'Warm'}]},{'list_id':'3.0'},{'skip_duplicates':'no'},{'skip_duplicates':None},{'file_name':False},{'new_list_name':None}]:inputs.append({'url':'https://csv.example.net/list.csv',**extra})
for value in inputs:
 try:
  dto=ns['ContactImportURLRequest'].model_validate(value);dump=dto.model_dump();cases.append(dict(input=value,ok=True,expected={'url':dump['url'],'filename':dump['file_name'],'options':dict(list_id=dump['list_id'],new_list_name=dump['new_list_name'],skip_duplicates=dump['skip_duplicates'],column_mapping=dump['column_mapping'] or {},auto_tag_rules=dump['auto_tag_rules'],tags=ns['split_tags'](', '.join(dump['tags'] or [])))}))
 except cg.ValidationError:cases.append(dict(input=value,ok=False))
ops=[]
async def main():
 original_lookup=socket.getaddrinfo;original_httpx=sys.modules.get('httpx')
 try:
  for case in [dict(url='file:///etc/passwd'),dict(url='https://user:pass@csv.example.net/x'),dict(url='http://localhost/x'),dict(url='http://metadata.google.internal/x'),dict(url='https://csv.example.net/x',addresses=['127.0.0.1']),dict(url='https://csv.example.net/x',addresses=['8.8.8.8','10.0.0.1']),dict(url='https://csv.example.net/x',dns_error=True),dict(url='https://csv.example.net/x',status=302),dict(url='https://csv.example.net/x',status=404),dict(url='https://csv.example.net/x',length=str(20*1024*1024+1)),dict(url='https://csv.example.net/x',transport_error=True),dict(url='https://csv.example.net/x',body='Email,Mood\nremote@business.ng,warm\n',new_list_name=' Remote ',tags=['Buyer','buyer'],auto_tag_rules=[{'field':'Mood','contains':'warm','tag':'Warm'}]),dict(url='https://csv.example.net/x',body='',new_list_name='Rollback remote'),dict(url='https://csv.example.net/x',body='Email\nx@business.ng\n',list_id=404)]:
   engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
   async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
   def lookup(*args,**kw):
    if case.get('dns_error'):raise OSError('synthetic DNS failure')
    return [(2,1,6,'',(ip,443)) for ip in case.get('addresses',['8.8.8.8'])]
   socket.getaddrinfo=lookup
   class Response:
    status_code=case.get('status',200);headers={'content-length':case['length']} if 'length' in case else {}
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def aiter_bytes(self):yield case.get('body','Email\nx@business.ng\n').encode()
   class Client:
    def __init__(self,**kw):assert kw==dict(timeout=20,follow_redirects=False)
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def stream(self,method,url):
     assert method=='GET'
     if case.get('transport_error'):raise OSError('synthetic transport failure')
     return Response()
   sys.modules['httpx']=types.SimpleNamespace(AsyncClient=Client)
   sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
   async with sessions() as db:
    fields={k:v for k,v in case.items() if k in ('url','new_list_name','tags','auto_tag_rules','list_id')};dto=ns['ContactImportURLRequest'].model_validate(fields)
    try:
     out=await ns['create_csv_import_job_from_url'](data=dto,background_tasks=jobs.Background(),db=db,current_user=jobs.owner)
     job=await db.get(ns['ContactImportJob'],out['id']);result=dict(status=202,body=json.loads(jobs.normalize(out)),content=job.content,mapping=json.loads(job.mapping_json),tags=json.loads(job.tags_json),rules=json.loads(job.auto_tag_rules_json))
    except cg.HTTPException as e:await db.rollback();result=dict(status=e.status_code,message=e.detail)
    ops.append(dict(case=case,expected=result,snapshot=await jobs.ref.snapshot(db)))
   await engine.dispose()
 finally:
  socket.getaddrinfo=original_lookup
  if original_httpx is None:sys.modules.pop('httpx',None)
  else:sys.modules['httpx']=original_httpx
 (D/'tests/url-import-source-fixtures.json').write_text(json.dumps(dict(dto=cases,operations=ops,scope='Actual original URL DTO and endpoint with forced fake DNS/httpx; isolated SQLite. Not real network, TLS, cURL, WP, MySQL, SSRF bypass or browser certification.'),indent=2,ensure_ascii=False)+'\n')
 print(len(cases),'original DTO cases;',len(ops),'original URL operations with fake DNS/httpx')
if __name__=='__main__':asyncio.run(main())
