#!/usr/bin/env python3
"""Selected real campaign create/update/pause/stop/delete/duplicate AST; no app/config/provider imports."""
import ast,asyncio,datetime,importlib.util,json,logging,types
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('campaign_mutation_reference',D/'tools/generate_campaign_report_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
ns.update(logger=logging.getLogger('offline-campaign-controls'),Request=object,update=cg.sa.update)
cg.load('backend/app/services/campaign_service.py',{'CampaignError','CampaignNotFound','CampaignStateError'})
cls=next(n for n in ast.parse((ROOT/'backend/app/services/campaign_service.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='CampaignService')
methods={'__init__','_get','transition','create_campaign','pause_campaign','stop_campaign','delete_campaign'}
cls.body=[n for n in cls.body if isinstance(n,ast.Assign) or isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in methods];cls=cg.StripLocalAppImports().visit(cls)
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'source-selected-campaign-service','exec'),ns)
ns['EDITABLE_STATUSES']={'draft','scheduled'}

# DTOs use real datetime as field type but fixed time only inside the selected future validator.
dto=dict(ns,datetime=datetime.datetime,timedelta=datetime.timedelta);constants=[n for n in ast.parse((ROOT/'backend/app/schemas/campaign.py').read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id',None)=='SENDER_DISTRIBUTIONS' for t in n.targets)];exec(compile(ast.Module(body=constants,type_ignores=[]),'source-campaign-distributions','exec'),dto);cg.load('backend/app/schemas/campaign.py',{'_clean_sender_distribution','_require_future','CampaignCreate','CampaignUpdate'},dto);dto['_require_future'].__globals__['datetime']=cg.Clock
ns['CampaignUpdate']=dto['CampaignUpdate']
cg.load('backend/app/api/v1/campaigns.py',{'update_campaign','duplicate_campaign','_unique_copy_name','pause_campaign','stop_campaign','delete_campaign','_problem'})
# The annotation was resolved when classes were defined; restore datetime for model fields after load.
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);ops=[];seeds=[];utc_schedule={}
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();
   if model=='Campaign' and row.scheduled_start_at is not None:utc_schedule[row.id]=row.scheduled_start_at.astimezone(datetime.timezone.utc)
   seeds.append(dict(table=row.__tablename__,values={c.name:cg.dump(getattr(row,c.name)) for c in row.__table__.columns}));return row
  await seed('ContactList',name='Campaign audience');await seed('Contact',phone_number='+2348031234567');await seed('Conversation',contact_id=1)
  for status in ('draft','scheduled','running','paused','completed','stopped','failed'):
   await seed('Campaign',name='State '+status,status=status,list_id=1,message_body='Original text',messages_sent=0,total_contacts=5,scheduled_start_at=cg.NOW+datetime.timedelta(days=1) if status=='scheduled' else None)
  await seed('Campaign',name='Scheduled hold',status='paused',paused_from='scheduled',scheduled_start_at=cg.NOW+datetime.timedelta(days=1))
  await seed('Campaign',name='Already sent',status='failed',messages_sent=1)
  await seed('Campaign',name='Stop fixture',status='running')
  for i,status in enumerate(('queued','retrying','sending','sent','delivered','unknown','failed','cancelled')):
   await seed('Message',campaign_id=10,contact_id=1,conversation_id=1,direction='outgoing',body='Stopped source '+status,status=status,idempotency_key='stop-fixture-'+str(i))
  for status in ('pending','queued','sent','failed','replied'):
   await seed('CampaignContact',campaign_id=10,contact_id=len(seeds),status=status,next_action_at=cg.NOW+datetime.timedelta(hours=1))
  for status in ('pending','sent','failed'):
   await seed('FollowUp',campaign_id=10,contact_id=1,status=status,scheduled_at=cg.NOW+datetime.timedelta(hours=1))
  await db.commit()
  scenarios=[dict(kind='create',body={'name':'Basic'}),dict(kind='create',body={'name':'Paced SMS','message_body':'  Hello {{first_name}}  ','list_id':1,'daily_limit':12,'hourly_limit':5,'per_minute_limit':2,'min_delay':1,'max_delay':4,'send_start_hour':8,'send_end_hour':18,'allow_weekends':False,'timezone_name':' Africa/Lagos ','allowed_weekdays':' 0,1,2 ','scheduled_start_at':'2026-01-03T09:00:00+01:00'}),dict(kind='update',id=1,body={'message_body':'  Edited final text  '}),dict(kind='update',id=2,body={'description':'Changed'}),dict(kind='update',id=1,body={'list_id':1}),dict(kind='duplicate',id=1),dict(kind='duplicate',id=1),dict(kind='pause',id=3),dict(kind='pause',id=2),dict(kind='stop',id=10)]
  for kind in ('pause','stop','delete'):
   for id in (1,4,5,6,7,8,9,404):scenarios.append(dict(kind=kind,id=id))
  scenarios.extend([dict(kind='delete',id=11),dict(kind='read',id=11),dict(kind='update',id=5,body={'name':'Cannot edit'}),dict(kind='update',id=404,body={'name':'Missing'})])
  for case in scenarios:
   try:
    if case['kind']=='create':
     d=dto['CampaignCreate'].model_validate(case['body']);payload=d.model_dump(exclude={'idempotency_key'});row=await ns['CampaignService'](db).create_campaign(payload);out=ns['_campaign_out'](row)
     if row.scheduled_start_at is not None:utc_schedule[row.id]=row.scheduled_start_at.astimezone(datetime.timezone.utc)
    elif case['kind']=='update':out=await ns['update_campaign'](case['id'],dto['CampaignUpdate'].model_validate(case['body']),db,None)
    elif case['kind']=='read':out=await ns['get_campaign'](case['id'],db,None)
    else:out=await ns[case['kind']+'_campaign'](case['id'],db,None)
    await db.commit();expected=cg.dump(out);status=201 if case['kind']=='create' else (204 if case['kind']=='delete' else 200)
   except cg.HTTPException as e:await db.rollback();expected=e.detail;status=e.status_code
   async def snapshot():
    result={}
    for name,model in [('campaigns','Campaign'),('messages','Message'),('campaign_contacts','CampaignContact'),('followups','FollowUp')]:
     rows=(await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all();result[name]=[{c.name:cg.dump(utc_schedule[r.id] if model=='Campaign' and c.name=='scheduled_start_at' and r.id in utc_schedule and r.scheduled_start_at is not None else getattr(r,c.name)) for c in r.__table__.columns} for r in rows]
    return result
   ops.append(dict(**case,status=status,expected=expected,state=await snapshot()))
 # Actual DTO accept/reject decisions; constrained type/null safety divergences are explicit in the PHP test.
 cases=[]
 for body in ({},{'name':'x'},{'name':''},{'name':'x'*256},{'name':'x','daily_limit':0},{'name':'x','send_start_hour':24},{'name':'x','channel':'fax'},{'name':'x','sender_distribution':' WEIGHTED '},{'name':'x','sender_distribution':'bad'},{'name':'x','scheduled_start_at':'2025-01-01T00:00:00Z'},{'name':'x','scheduled_start_at':'2026-01-03T08:00:00Z'}):
  try:d=dto['CampaignCreate'].model_validate(body);cases.append(dict(body=body,accepted=True,normalized=cg.dump(d.model_dump())))
  except Exception:cases.append(dict(body=body,accepted=False))
 (D/'tests/campaign-controls-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,dto_cases=cases,scope='Selected actual source service/API/DTO mutation AST only; fixed-clock SQLite, no native FKs, email service, launch worker or idempotency oracle. Explicit safety divergences tested separately.'),indent=2)+'\n');await engine.dispose()
asyncio.run(main())
