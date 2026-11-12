#!/usr/bin/env python3
"""Actual selected original job API/worker AST against isolated SQLite; no app imports/egress."""
import asyncio,base64,csv,importlib.util,io,json,re,types
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('offline_import_reference',D/'tools/generate_import_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
cg.load('backend/app/models/import_job.py',{'ContactImportJob'},models=True)
class Background:
 def add_task(self,*args):pass
class Download:
 def __init__(self,content,media_type,headers):self.body=''.join(content);self.media_type=media_type;self.headers=headers
ns.update(BackgroundTasks=Background,StreamingResponse=Download,uuid=types.SimpleNamespace(uuid4=lambda:'00000000-0000-4000-8000-000000000001'))
cg.load('backend/app/api/v1/contacts.py',{'_import_job_dict','_create_import_job','_run_contact_import_job','create_csv_import_job','read_csv_import_job','download_csv_import_errors'})
owner=types.SimpleNamespace(id=41,role='operator');stranger=types.SimpleNamespace(id=42,role='operator');admin=types.SimpleNamespace(id=42,role='admin')
job_id='00000000-0000-4000-8000-000000000001'
progress=[];tracking=False
class TrackingSession(cg.AsyncSession):
 async def commit(self):
  await super().commit()
  if tracking:
   job=await self.get(ns['ContactImportJob'],job_id)
   if job:
    progress.append(dict(status=job.status,processed_rows=job.processed_rows,total_rows=job.total_rows,imported=job.imported,invalid=job.invalid,duplicates=job.duplicates,merged=job.merged,errors=len(json.loads(job.errors_json)),contacts=(await self.execute(cg.sa.select(cg.sa.func.count(ns['Contact'].id)))).scalar(),members=(await self.execute(cg.sa.select(cg.sa.func.count(ns['ContactListMember'].id)))).scalar(),stored_list_count=(await self.execute(cg.sa.select(ns['ContactList'].contact_count))).scalar()))
def normalize(value):
 # SQLite drops DateTime timezone metadata after refresh; model these known UTC fixture dates explicitly.
 def dates(v):
  if isinstance(v,dict):return {k:(x+'+00:00' if k in ('created_at','started_at','completed_at') and isinstance(x,str) and re.fullmatch(r'2026-01-02T12:00:00\.123456',x) else dates(x)) for k,x in v.items()}
  if isinstance(v,list):return [dates(x) for x in v]
  return v
 return re.sub(r'00000000-0000-4000-8000-000000000001','<job-id>',json.dumps(dates(cg.dump(value)),ensure_ascii=False))
async def main():
 global tracking
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False,class_=TrackingSession);ns['async_session_factory']=sessions
 text=io.StringIO();writer=csv.writer(text);writer.writerow(['Email','Phone','Mood'])
 for i in range(105):writer.writerow([f'person{i}@business.ng','','warm'])
 for i in range(52):writer.writerow(['',('bad,\n"phone"' if i==0 else 'bad'),''])
 writer.writerow(['person0@business.ng','','not a merge'])
 content=text.getvalue().encode();filename='é'*260+'.csv';options=dict(new_list_name=' Queue ',tags='VIP',auto_tag_rules='[{"field":"Mood","contains":"warm","tag":"Warm"}]')
 operations=[]
 async def call(kind,**extra):
  async with sessions() as db:
   try:
    if kind=='create':
     upload=ref.Upload(content if 'csv' not in extra else base64.b64decode(extra['csv']));upload.filename=filename
     job=await ns['create_csv_import_job'](background_tasks=Background(),file=upload,db=db,current_user=owner,**(options|extra.get('options',{})));out=dict(status=202,body=job);await db.commit()
    elif kind=='read':out=dict(status=200,body=await ns['read_csv_import_job'](job_id=job_id,db=db,current_user={'owner':owner,'stranger':stranger,'admin':admin}[extra.get('user','owner')]))
    elif kind=='errors':
     value=await ns['download_csv_import_errors'](job_id=job_id,db=db,current_user={'owner':owner,'stranger':stranger}[extra.get('user','owner')]);out=dict(status=200,body=value.body,headers=value.headers)
   except cg.HTTPException as e:await db.rollback();out=dict(status=e.status_code,message=e.detail)
   op=dict(kind=kind,**extra,expected=json.loads(normalize(out)))
   if kind=='create':op['snapshot']=await ref.snapshot(db)
   operations.append(op)
 await call('create');await call('read');await call('read',user='stranger');await call('errors',user='stranger');await call('errors')
 tracking=True;await ns['_run_contact_import_job'](job_id);tracking=False
 async with sessions() as db:
  job=await db.get(ns['ContactImportJob'],job_id)
  operations.append(dict(kind='run',progress=progress,snapshot=await ref.snapshot(db),content_cleared=job.content is None))
 await call('read');await call('read',user='admin');await call('errors')
 # Creation failures must not add destination lists or jobs.
 for extra in [dict(csv=''),dict(options={'column_mapping':'['}),dict(options={'column_mapping':'[]'}),dict(options={'auto_tag_rules':'{}'}),dict(options={'list_id':404}),dict(options={'new_list_name':'queue'})]:await call('create',**extra)
 await engine.dispose()
 (D/'tests/import-jobs-source-fixtures.json').write_text(json.dumps(dict(csv=base64.b64encode(content).decode(),filename=filename,options=options,operations=operations,scope='Selected original queue/create/read/errors/worker functions; fixed clock and UUID, synthetic isolated SQLite (known fixture UTC timezone restored after SQLite refresh) and omitted relationships. Not real background tasks, SAPI uploads, native MySQL locks, WordPress cron, HTTP or concurrency proof.'),indent=2,ensure_ascii=False)+'\n')
 print(len(operations),'original job operations;',len(progress),'durable worker commits')
if __name__=='__main__':asyncio.run(main())
