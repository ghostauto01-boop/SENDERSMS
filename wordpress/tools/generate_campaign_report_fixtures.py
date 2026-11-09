#!/usr/bin/env python3
"""Actual selected classic campaign read handlers/DTO/stats, synthetic offline SQLite."""
import ast,asyncio,base64,datetime,importlib.util,json,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('report_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
cg.load('backend/app/models/campaign.py',{'CampaignContact'},models=True)
cg.load('backend/app/models/campaign_followup.py',{'CampaignFollowUp','CampaignFollowUpLog'},models=True)
cg.load('backend/app/models/scheduled.py',{'ScheduledMessage'},models=True)
dto=dict(ns,datetime=datetime.datetime);cg.load('backend/app/schemas/campaign.py',{'CampaignOut'},dto);ns['CampaignOut']=dto['CampaignOut']
ns.update(MAX_ATTACHMENTS=10,MAX_ATTACHMENT_BYTES=4*1024*1024,MAX_ATTACHMENTS_TOTAL_BYTES=8*1024*1024,base64=base64)
cg.load('backend/app/services/email_service.py',{'clean_attachments','load_attachments','attachment_summary'})
cls=next(n for n in ast.parse((ROOT/'backend/app/services/campaign_service.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='CampaignService');method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='get_campaign_stats');exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'actual-source-get-campaign-stats','exec'),ns)
class SelectedStats:
 def __init__(self,db):self.db=db
 get_campaign_stats=ns['get_campaign_stats']
ns['CampaignService']=SelectedStats
ns['_NameLike']=object;ns.update(and_=cg.sa.and_,or_=cg.sa.or_,Iterable=typing.Iterable,Mapping=typing.Mapping,Any=typing.Any);cg.load('backend/app/utils/naming.py',{'contact_display_name'});cg.load('backend/app/mcp/execution_guard.py',{'reconcile_campaigns_and_followups'});cg.load('backend/app/api/v1/campaigns.py',{'_campaign_out','list_campaigns','get_campaign','get_campaign_analytics','get_campaign_conversations','get_campaign_performance'})
(D/'sender-suite/schema/campaign-out-fields.json').write_text(json.dumps(list(ns['CampaignOut'].model_fields),indent=2)+'\n')
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  async def add(model,**v):
   r=ns[model](**v);db.add(r);await db.flush();seeds.append(dict(table=r.__tablename__,values={c.name:cg.dump(getattr(r,c.name)) for c in r.__table__.columns}));return r
  for i in range(8):
   await add('Campaign',name=['Welcome','Email launch','Buyer _ %','Stopped','Running SMS','Email paused','Empty stats','Unicode Lagos'][i],channel='email' if i%2 else 'sms',status=['draft','scheduled','completed','stopped','running','paused','failed','draft'][i],messages_sent=i*3,messages_delivered=i*2,messages_failed=i,replies=i,interested=i//2,total_contacts=i*5,updated_at=cg.NOW+datetime.timedelta(minutes=i),started_at=cg.NOW if i>1 else None,attachments=json.dumps([{'name':'private.txt','content_base64':'cHJpdmF0ZQ==','content_type':'text/plain'}]) if i%2 else None,email_account_ids=[1,-2] if i%2 else None,email_sender_weights={'1':2,'-2':1} if i%2 else None)
  for status in ('pending','sent','sent','failed','replied'):await add('CampaignContact',campaign_id=3,contact_id=len(seeds)+1,status=status)
  for i,name in enumerate(('Ada','  ','Email')):await add('Contact',first_name=name,last_name='Obi' if i==0 else None,business_name='Brand' if i==1 else None,email='lead'+str(i)+'@example.invalid')
  for i in range(5):
   v=await add('Conversation',contact_id=i+1,channel='email' if i==1 else 'sms',campaign_id=2 if i==1 else 3,last_campaign_id=1 if i==2 else None,last_message_at=cg.NOW-datetime.timedelta(minutes=i),last_message_preview='Thread '+str(i),unread_count=i,message_count=i+1)
   if i<3:
    for j in range(2):await add('Message',conversation_id=v.id,contact_id=i+1,channel=v.channel,direction='incoming',body='Reply '+str(i)+' '+str(j),ai_sentiment='positive',ai_intent='interested',created_at=cg.NOW-datetime.timedelta(minutes=i,seconds=2-j),idempotency_key='fixture-reply-'+str(i)+'-'+str(j))
  for idx,st in enumerate(('sent','delivered','failed','cancelled','queued','sending')):
   await add('Message',conversation_id=1,contact_id=1,campaign_id=3,channel='sms',direction='outgoing',status=st,body='Out '+str(idx),created_at=cg.NOW-datetime.timedelta(minutes=10+idx),idempotency_key='fixture-out-'+str(idx))
  opt_c=await add('Contact',first_name='Opt',last_name='Out',email='opt@example.invalid',is_opted_out=True)
  await add('CampaignContact',campaign_id=3,contact_id=opt_c.id,status='sent')
  await add('CampaignFollowUp',campaign_id=3,step_order=1,name='Follow 1',message_text='Hi',is_active=True)
  await add('CampaignFollowUp',campaign_id=3,step_order=2,name='Follow 2',message_text='Hi2',is_active=False)
  await add('CampaignFollowUpLog',followup_id=1,campaign_id=3,contact_id=1,status='sent')
  await add('ScheduledMessage',contact_id=1,phone_number='+2348031234567',body='Sched 1',schedule_at=cg.NOW+datetime.timedelta(hours=2),status='pending')
  await add('ScheduledMessage',contact_id=1,phone_number='+2348031234567',body='Sched 2',schedule_at=cg.NOW-datetime.timedelta(hours=1),status='sent')
  await db.commit()
 for q in ({},{'channel':'all'},{'channel':'email'},{'status':'draft'},{'search':'WELCOME'},{'search':'%'},{'search':'_'},{'channel':'all','page':2,'per_page':3},{'page':99},{'channel':'typo'}):
  async with sessions() as db:
   out=await ns['list_campaigns'](db=db,current_user=None,**q);ops.append(dict(kind='list',query=q,expected=cg.dump(out)))
 for kind in ('read','analytics','performance'):
  for id in (1,2,3,7,404):
   async with sessions() as db:
    fn={'read':'get_campaign','analytics':'get_campaign_analytics','performance':'get_campaign_performance'}[kind]
    try:out=await ns[fn](campaign_id=id,db=db,current_user=None);ops.append(dict(kind=kind,id=id,status=200,expected=cg.dump(out)))
    except cg.HTTPException as e:ops.append(dict(kind=kind,id=id,status=e.status_code,expected=e.detail))
 for cids in (None,[1,3,999]):
  async with sessions() as db:
   out=await ns['reconcile_campaigns_and_followups'](db,campaign_ids=cids,include_scheduled_messages=True)
   ops.append(dict(kind='reconcile',query={} if cids is None else {'campaign_ids':','.join(str(x) for x in cids)},status=200,expected=cg.dump(out)))
 for id in (1,2,3,7,404):
  for q in ({},{'replied_only':True},{'page':2,'per_page':1}):
   async with sessions() as db:
    try:out=await ns['get_campaign_conversations'](campaign_id=id,db=db,current_user=None,**q);ops.append(dict(kind='conversations',id=id,query=q,status=200,expected=cg.dump(out)))
    except cg.HTTPException as e:ops.append(dict(kind='conversations',id=id,query=q,status=e.status_code,expected=e.detail))
 (D/'tests/campaign-reports-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,scope='Actual selected source classic campaign reads/DTO and stats method; fixed clock, synthetic SQLite, omitted ORM relationships/FKs. Not campaign execution, reconciliation, providers or native tests.'),indent=2)+'\n');await engine.dispose()
asyncio.run(main())
