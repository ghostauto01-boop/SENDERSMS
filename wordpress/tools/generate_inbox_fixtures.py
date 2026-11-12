#!/usr/bin/env python3
"""Selected actual inbox + attribution AST. Synthetic DB/clock only; no app/SMSService imports or sends."""
import ast,asyncio,datetime,importlib.util,json,types
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_inbox_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
ns.update(_NameLike=object,json=json,Float=cg.sa.Float,dt=cg.Clock,tz=datetime.timezone,update=cg.sa.update)
class HTTPException(cg.HTTPException):
 def __init__(self,status_code,detail='Not Found'):super().__init__(status_code,detail)
ns['HTTPException']=HTTPException
cg.load('backend/app/models/campaign.py',{'CampaignContact'},models=True);cg.load('backend/app/models/ads.py',{'_now'});cg.load('backend/app/models/ads.py',{'AdsCampaign'},models=True)
cg.load('backend/app/utils/naming.py',{'contact_display_name'});cg.load('backend/app/utils/phone.py',{'phone_search_variants'})
cg.load('backend/app/services/attribution.py',{'backfill_conversation','campaign_label_map','conversation_campaign','conversation_last_campaign'})
# Use the actual two SQL statements from _stop_seq, without importing the sender or its dependencies.
node=next(n for n in ast.parse((ROOT/'backend/app/services/sms_service.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='SMSService');method=next(n for n in node.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_stop_seq')
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='SMSService',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[])),'source-selected-stop-seq','exec'),ns)
ns['SMSService'].__init__=lambda self,db:setattr(self,'db',db)
ns['OPPORTUNITY_STATUSES']=('active','unread','interested','not_interested','closed','opportunity')
cg.load('backend/app/api/v1/inbox.py',{'_custom_fields','list_conversations','campaign_filters','get_conversation','_get_conv','_set_status','mark_interested','mark_not_interested','close_conversation','mark_opportunity','set_conversation_status','mark_unread','stop_seq','resume_seq'})
ops=[]
def add(kind,**kw):ops.append(dict(kind=kind,**kw))
add('filters');add('list');
for search in ['+234803','803111','Ada','Bola','%','٠٨٠٣','not a name 0803111']:add('list',q={'search':search,'channel':'all'})
add('list',q={'channel':'email'});add('list',q={'channel':'all','per_page':2});add('list',q={'channel':'all','page':2,'per_page':2});add('list',q={'search':'0803111'});add('list',q={'status':'unread'});add('list',q={'campaign_id':2});add('list',q={'ads_campaign_id':1});add('list',q={'replied_only':True});add('filters',q={'channel':'all'});add('get',id=1);add('action',id=1,action='mark-interested');add('get',id=1);add('action',id=1,action='mark-unread');add('action',id=1,action='status',body={'status':' active '});add('action',id=1,action='mark-opportunity');add('action',id=1,action='stop-sequence');add('get',id=1);add('action',id=1,action='resume-sequence');add('action',id=1,action='mark-not-interested');add('action',id=1,action='mark-close');add('get',id=1);add('action',id=2,action='status',body={'status':'active'});add('action',id=2,action='status',body={'status':'read'});add('list',q={'channel':'invalid'});add('list',q={'search':'Missing'});add('list',q={'channel':'all','status':'all'});add('filters');add('get',id=404);add('action',id=404,action='mark-unread')
async def snapshot(db):
 out={}
 for model,fields in [('Conversation',['id','status','unread_count','sequence_paused','campaign_id','ads_campaign_id','last_campaign_id','last_ads_campaign_id']),('Contact',['id','lead_status']),('FollowUp',['id','contact_id','status']),('CampaignContact',['id','contact_id','status','next_action_at'])]:out[ns[model].__tablename__]=[{k:cg.dump(getattr(r,k)) for k in fields} for r in (await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all()]
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[]
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={col.name:cg.dump(getattr(row,col.name)) for col in row.__table__.columns}));return row
  await seed('Contact',first_name='Ada',last_name='Obi',phone_number='+2348031112222',custom_fields='{"project":"Native"}');await seed('Contact',business_name='Bola Ltd',email='bola@business.ng',custom_fields='malformed');await seed('Contact',first_name='Chidi',email='chidi@business.ng',lead_status='customer')
  await seed('Campaign',name='First');await seed('Campaign',name='Second',status='running');await seed('AdsCampaign',name='Ads first')
  for i,ch,status,unread in [(1,'sms','unread',3),(2,'email','opportunity',1),(3,'sms','active',0)]:await seed('Conversation',contact_id=i,channel=ch,status=status,unread_count=unread,message_count=3,last_message_preview='Preview '+str(i),last_message_at=cg.NOW-datetime.timedelta(hours=i))
  for n,(conv,channel,direction,campaign,ads) in enumerate([(1,'sms','outgoing',1,None),(1,'sms','outgoing',2,None),(1,'sms','incoming',None,None),(2,'email','outgoing',None,1),(2,'email','incoming',None,None),(3,'sms','outgoing',None,None)]):await seed('Message',conversation_id=conv,contact_id=conv,channel=channel,direction=direction,campaign_id=campaign,ads_campaign_id=ads,body='Synthetic '+str(n),status='received' if direction=='incoming' else 'sent',idempotency_key='synthetic-inbox-'+str(n),last_error='provider secret-looking error' if n==1 else None,created_at=cg.NOW+datetime.timedelta(minutes=n))
  for contact,ch,status in [(1,'sms','pending'),(1,'email','sending'),(1,'sms','sent'),(2,'sms','pending')]:await seed('FollowUp',contact_id=contact,channel=ch,status=status,scheduled_at=cg.NOW)
  for contact,status in [(1,'pending'),(1,'queued'),(1,'sent'),(1,'failed'),(2,'pending')]:await seed('CampaignContact',campaign_id=1,contact_id=contact,status=status,next_action_at=cg.NOW)
  await db.commit()
 for op in ops:
  async with sessions() as db:
   try:
    kind=op['kind'];kw=dict(db=db,cu=None)
    if kind=='list':
     q=dict(page=1,per_page=500,status=None,search=None,channel='sms',campaign_id=None,ads_campaign_id=None,replied_only=False);q.update(op.get('q',{}));out=await ns['list_conversations'](**q,**kw)
    elif kind=='filters':out=await ns['campaign_filters'](channel=op.get('q',{}).get('channel','sms'),**kw)
    elif kind=='get':out=await ns['get_conversation'](conversation_id=op['id'],**kw)
    else:
     fn={'mark-interested':'mark_interested','mark-not-interested':'mark_not_interested','mark-close':'close_conversation','mark-opportunity':'mark_opportunity','mark-unread':'mark_unread','stop-sequence':'stop_seq','resume-sequence':'resume_seq','status':'set_conversation_status'}[op['action']];args=dict(conversation_id=op['id'],**kw)
     if fn=='set_conversation_status':args['payload']=op['body']
     out=await ns[fn](**args)
    op['expected']=dict(status=200,body=cg.dump(out));await db.commit()
   except HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
  async with sessions() as db:op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/inbox-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,scope='Actual selected inbox/attribution AST and two original stop-sequence statements. Synthetic SQLite/fixed UTC clock; omitted FKs/relationships, no original app/provider imports. No native WordPress/MySQL/HTTP/concurrency/receive/reply proof.'),indent=2,ensure_ascii=False)+'\n');print(len(ops),'source inbox operations;',len(seeds),'synthetic seeds')
if __name__=='__main__':asyncio.run(main())
