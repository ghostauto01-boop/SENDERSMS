#!/usr/bin/env python3
"""Selected actual analytics/metrics AST; isolated fixed-clock SQLite without FK/relationship simulation."""
import ast,asyncio,datetime,importlib.util,itertools,json,types
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_contacts_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg);ns=cg.ns
# These fixtures intentionally omit FK targets and relationships, not native constraint evidence.
def visit_call(self,node):
 self.generic_visit(node)
 if getattr(node.func,'id','')=='mapped_column':node.args=[a for a in node.args if not(isinstance(a,ast.Call) and getattr(a.func,'id','')=='ForeignKey')]
 return node
cg.StripLocalAppImports.visit_Call=visit_call
ns.update(JSON=cg.sa.JSON,case=cg.sa.case,timedelta=datetime.timedelta)
for path,names in [('campaign',{'Campaign'}),('conversation',{'Conversation','Message'}),('followup',{'FollowUp'}),('call',{'CallLog'})]:cg.load('backend/app/models/'+path+'.py',names,models=True)
metric_names={'DEFINITIONS','OUTGOING','INCOMING','DELIVERED','FAILED','CANCELLED','QUEUED','OPENED','CLICKED'}
tree=ast.parse((ROOT/'backend/app/services/message_metrics.py').read_text());nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in metric_names for t in n.targets)]
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-metric-constants','exec'),ns)
(D/'sender-suite/schema/message-metrics.json').write_text(json.dumps(ns['DEFINITIONS'],indent=2)+'\n')
cg.load('backend/app/services/message_metrics.py',{'window_start','scope','channel_condition','message_filters','rate','message_counts'})
ns['metrics']=types.SimpleNamespace(**{name:ns[name] for name in metric_names|{'window_start','scope','message_filters','rate','message_counts'}})
ns['CHANNELS']=('sms','email');cg.load('backend/app/utils/channels.py',{'validate_channel'})
cg.load('backend/app/api/v1/analytics.py',{'_day_floor','get_analytics_overview','get_timeseries','get_funnel','get_by_campaign','get_call_stats'})
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];operations=[]
 async with sessions() as db:
  async def seed(model,**values):
   if model=='Message':values['idempotency_key']='synthetic-analytics-'+str(len(seeds))
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={col.name:cg.dump(getattr(row,col.name)) for col in row.__table__.columns}));return row
  for i in range(6):await seed('Contact',email=f'analytics{i}@business.ng',lead_status='interested' if i<2 else 'new',is_opted_out=i==0,opted_out_at=cg.NOW if i==0 else None,is_undeliverable=i<3,undeliverable_reason='quarantine' if i<2 else ('é'*90 if i==2 else None))
  await seed('Campaign',name='SMS campaign');await seed('Campaign',name='Email campaign',channel='email')
  await seed('Conversation',contact_id=1)
  for i,(channel,direction,status) in enumerate(itertools.product(('sms','email'),('outgoing','incoming'),('sent','delivered','failed','cancelled','queued','sending','retrying'))):
   await seed('Message',contact_id=i%6+1,conversation_id=1,campaign_id=1 if channel=='sms' else 2,channel=channel,direction=direction,status=status,body='synthetic analytics',last_error='Failure reason' if status=='failed' else None,created_at=cg.NOW-datetime.timedelta(days=i%4),delivered_at=cg.NOW if i==0 else None,opened_at=cg.NOW if i==2 else None,open_count=2 if i==4 else 0,click_count=3 if i==6 else 0)
  await seed('Message',contact_id=1,conversation_id=1,campaign_id=99,direction='outgoing',status='failed',body='historical missing campaign',created_at=cg.NOW,last_error='é'*90)
  await seed('Message',contact_id=1,conversation_id=1,campaign_id=None,direction='incoming',status='received',body='unattributed reply',created_at=cg.NOW.replace(hour=0,minute=0,second=0,microsecond=0))
  await seed('Message',contact_id=1,conversation_id=1,direction='outgoing',status='sent',body='window edge',created_at=cg.NOW.replace(hour=0,minute=0,second=0,microsecond=0)-datetime.timedelta(microseconds=1))
  for n in (0,1,31):await seed('FollowUp',contact_id=1,scheduled_at=cg.NOW,created_at=cg.NOW-datetime.timedelta(days=n))
  for n,status in enumerate(('ended','ended','failed','ringing','cancelled','no_answer')):await seed('CallLog',phone_number='+2348031112222',status=status,created_at=cg.NOW-datetime.timedelta(days=n))
  await db.commit()
  for path in ('overview','timeseries','funnel','by-campaign','calls'):
   fn=ns[{'overview':'get_analytics_overview','timeseries':'get_timeseries','funnel':'get_funnel','by-campaign':'get_by_campaign','calls':'get_call_stats'}[path]]
   for days,channel in itertools.product((1,2,30),('sms','email','all')):
    kw=dict(days=days,db=db,current_user=None)
    if path!='calls':kw['channel']=channel
    if path in ('overview','timeseries','funnel'):kw['campaign_id']=None
    if path=='by-campaign':kw['limit']=10
    out=await fn(**kw);operations.append(dict(path=path,days=days,channel=channel,campaign=None,limit=10,expected=cg.dump(out)))
   if path in ('overview','timeseries','funnel'):
    for campaign in (0,1,2,404):
     out=await fn(days=30,channel='all',campaign_id=campaign,db=db,current_user=None);operations.append(dict(path=path,days=30,channel='all',campaign=campaign,limit=10,expected=cg.dump(out)))
  counts=[]
  for channel,days,campaign in itertools.product((None,'sms','email'),(None,1,30),(None,0,1,2,404)):
   counts.append(dict(channel=channel,days=days,campaign=campaign,expected=await ns['message_counts'](db,channel=channel,days=days,campaign_id=campaign)))
 await engine.dispose()
 (D/'tests/analytics-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=operations,counts=counts,scope='Actual selected source analytics/metrics AST; fixed UTC clock, synthetic SQLite with omitted FKs/relationships. Not native WordPress/MySQL/timezone/collation/HTTP/provider or browser evidence.'),indent=2,ensure_ascii=False)+'\n');print(len(operations),'source analytics operations;',len(counts),'source metrics scopes')
if __name__=='__main__':asyncio.run(main())
