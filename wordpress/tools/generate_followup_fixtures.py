#!/usr/bin/env python3
"""Selected original scheduling routes/DTO, synthetic SQLite/clock and explicitly empty sender inventory. Never sends."""
import ast,asyncio,datetime,importlib.util,json,types,typing
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('followup_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
cg.load('backend/app/models/campaign.py',{'CampaignContact'},models=True)
ns.update(timedelta=datetime.timedelta,ZoneInfo=ZoneInfo,ZoneInfoNotFoundError=ZoneInfoNotFoundError,settings=types.SimpleNamespace(DEFAULT_TIMEZONE='Africa/Lagos'),_NameLike=object)
cg.load('backend/app/utils/datetime.py',{'local_day_utc_bounds'});cg.load('backend/app/utils/naming.py',{'contact_display_name'})
ns['datetime']=datetime.datetime;cg.load('backend/app/schemas/followup.py',{'FollowUpCreate'});ns['datetime']=cg.Clock
# The original serializer imports datetime inside the function; inject the same fixed test clock there too.
class ClockImports(cg.StripLocalAppImports):
 def visit_ImportFrom(self,n):return None if n.module=='datetime' else super().visit_ImportFrom(n)
nodes=[]
for node in ast.parse((ROOT/'backend/app/api/v1/followups.py').read_text()).body:
 if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in {'_utc_iso','_followup_item','list_followups','create_followup','delete_followup','skip_followup','reschedule_followup'}:
  node.decorator_list=[];nodes.append(ClockImports().visit(node))
ns['status']=types.SimpleNamespace(HTTP_201_CREATED=201)
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'selected-original-followups','exec'),ns)
async def no_sender(*args):return None
ns['email_service']=types.SimpleNamespace(get_sender=no_sender,get_default_sender=no_sender)
async def snapshot(db):
 out={}
 for model,fields in [('FollowUp',['id','contact_id','campaign_id','campaign_contact_id','status','scheduled_at','executed_at','attempt_count','message_id','last_error']),('CampaignContact',['id','contact_id','campaign_id','status','next_action_at'])]:
  out[ns[model].__tablename__]=[{k:cg.dump(getattr(r,k)) for k in fields} for r in (await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all()]
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  async def seed(model,**values):
   r=ns[model](**values);db.add(r);await db.flush();seeds.append(dict(table=r.__tablename__,values={c.name:cg.dump(getattr(r,c.name)) for c in r.__table__.columns}));return r
  await seed('Contact',first_name='Ada',last_name='Obi',phone_number='+2348031112222',email='ada@business.ng');await seed('Contact',business_name='Bola Ltd',email='bola@business.ng');await seed('Contact',email='optout@business.ng',is_opted_out=True,is_email_opted_out=True);await seed('Contact',phone_number='+2348031113333')
  await seed('Campaign',name='Followup source');await seed('CampaignContact',campaign_id=1,contact_id=1,status='queued',next_action_at=cg.NOW);await seed('CampaignContact',campaign_id=1,contact_id=2,status='sent',next_action_at=cg.NOW)
  start,end=ns['local_day_utc_bounds']()
  times=[start-datetime.timedelta(microseconds=1),start,cg.NOW-datetime.timedelta(hours=1),cg.NOW,cg.NOW+datetime.timedelta(hours=1),end-datetime.timedelta(microseconds=1),end,end+datetime.timedelta(days=7)]
  for i,t in enumerate(times):await seed('FollowUp',contact_id=1,scheduled_at=t,message_text='SMS '+str(i),channel='sms')
  for i,t in enumerate(times[:4]):await seed('FollowUp',contact_id=2,scheduled_at=t,message_text='Email '+str(i),channel='email',subject='Subject',notify_on_due=True)
  for i,state in enumerate(('sending','sent','delivered','failed','skipped','cancelled')):await seed('FollowUp',contact_id=4,scheduled_at=cg.NOW-datetime.timedelta(minutes=i),message_text='Historical '+str(i),status=state,last_error='private-provider-reason' if state=='failed' else None)
  await seed('FollowUp',contact_id=99,scheduled_at=cg.NOW,message_text='Legacy orphan')
  await seed('FollowUp',contact_id=1,campaign_id=1,campaign_contact_id=1,scheduled_at=end,message_text='Linked skip')
  await seed('FollowUp',contact_id=2,campaign_id=1,campaign_contact_id=2,scheduled_at=end,message_text='Linked delete')
  await db.commit()
 for view in ('due_today','overdue','upcoming','pending','completed','skipped','all'):
  for channel in ('sms','email','all'):ops.append(dict(kind='list',q=dict(view=view,channel=channel)))
 ops.extend([dict(kind='list',q=dict(view='all',channel='all',per_page=3)),dict(kind='list',q=dict(view='all',channel='all',page=2,per_page=3)),dict(kind='list',q=dict(view='all',channel='all',page=100)),dict(kind='list',q=dict(view='bad')),dict(kind='list',q=dict(channel='invalid'))])
 future='2026-01-03T16:00:00+01:00';base=dict(contact_id=1,scheduled_at=future,message_text='  Native followup  ')
 for body in [base,dict(base,contact_id=2,channel='EMAIL',subject='  Future email  '),dict(base,contact_id=99),dict(base,contact_id=3),dict(base,channel='email',contact_id=3,subject='Email'),dict(base,channel='email',contact_id=4,subject='Email'),dict(base,channel='email'),dict(base,channel='email',subject='Email',email_account_id=99),dict(base,scheduled_at='2026-01-02T12:00:00Z')]:ops.append(dict(kind='create',body=body))
 ops.extend([dict(kind='action',id=20,action='skip'),dict(kind='action',id=20,action='skip'),dict(kind='action',id=20,action='reschedule',new_time=future),dict(kind='action',id=21,action='delete'),dict(kind='action',id=21,action='delete'),dict(kind='action',id=13,action='delete'),dict(kind='action',id=14,action='delete'),dict(kind='action',id=14,action='skip'),dict(kind='action',id=999,action='skip'),dict(kind='action',id=1,action='reschedule',new_time='not-a-date'),dict(kind='action',id=1,action='reschedule',new_time='2026-01-02T12:00:00'),dict(kind='action',id=1,action='reschedule',new_time='2026-01-01T12:00:00Z')])
 for op in ops:
  async with sessions() as db:
   try:
    if op['kind']=='list':
     q=dict(view='due_today',page=1,per_page=25,channel='sms');q.update(op.get('q',{}));out=await ns['list_followups'](**q,db=db,current_user=None)
    elif op['kind']=='create':out=await ns['create_followup'](data=ns['FollowUpCreate'].model_validate(op['body']),db=db,current_user=None)
    else:
     fn=ns[{'skip':'skip_followup','delete':'delete_followup','reschedule':'reschedule_followup'}[op['action']]];kw=dict(followup_id=op['id'],db=db,current_user=None)
     if op['action']=='reschedule':kw['new_time']=op['new_time']
     out=await fn(**kw)
    await db.commit();op['expected']=dict(status=201 if op['kind']=='create' else 204 if op.get('action')=='delete' else 200,body=cg.dump(out))
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code)
   op['snapshot']=await snapshot(db)
 dto=[]
 for body in [{},dict(base,message_text=' '),dict(base,message_text='x'*5001),dict(base,scheduled_at='2026-01-03T12:00:00'),dict(base,contact_id=0),dict(base,notify_on_due='true',max_attempts='2'),dict(base,max_attempts=11),dict(base,subject='x'*501),dict(base,channel=None),dict(base,message_text='😀'*5000),dict(base,extra_ignored=True)]:
  try:d=ns['FollowUpCreate'].model_validate(body);dto.append(dict(input=body,ok=True,expected=cg.dump(d.model_dump())))
  except cg.ValidationError:dto.append(dict(input=body,ok=False))
 bounds=[]
 for zone,now in [('Africa/Lagos','2026-01-02T00:00:00Z'),('UTC','2026-01-02T23:59:59Z'),('America/New_York','2026-03-08T12:00:00Z'),('America/New_York','2026-11-01T12:00:00Z'),('Asia/Kathmandu','2026-01-02T00:00:00Z'),('Pacific/Apia','2026-01-02T00:00:00Z')]:
  ns['settings'].DEFAULT_TIMEZONE=zone;a,b=ns['local_day_utc_bounds'](datetime.datetime.fromisoformat(now.replace('Z','+00:00')));bounds.append(dict(timezone=zone,now=now,start=cg.dump(a),end=cg.dump(b)))
 (D/'tests/followup-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,dto=dto,bounds=bounds,scope='Selected source with fixed clock/SQLite and empty sender test double; not sender resolution, transport, worker or native proof'),indent=2,ensure_ascii=False)+'\n');await engine.dispose()
asyncio.run(main())
