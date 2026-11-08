#!/usr/bin/env python3
"""Execute selected original signature/reconciler AST only. Fixed-clock SQLite, no app imports/providers."""
import ast,asyncio,datetime,hashlib,hmac,importlib.util,itertools,json,logging
from pathlib import Path
from unittest.mock import patch
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('receipt_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
cg.load('backend/app/models/campaign.py',{'CampaignContact'},models=True)
cg.load('backend/app/services/list_hygiene.py',{'mark_undeliverable','record_delivery_failure'})
source=ast.parse((ROOT/'backend/app/services/sms_service.py').read_text());cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='SMSService');method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='process_delivery_status');method=cg.StripLocalAppImports().visit(method)
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='ReceiptReference',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[])),'selected-original-reconciler','exec'),ns)
ns['ReceiptReference'].__init__=lambda self,db:setattr(self,'db',db)
source=ast.parse((ROOT/'backend/app/providers/smsgate.py').read_text());cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='SMSGateProvider');fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='validate_webhook_signature');fn.decorator_list=[]
exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'selected-original-hmac','exec'),ns)
secret='synthetic-receipt-signing-secret-0123456789';now=int(cg.NOW.timestamp());raw='{"event":"sms:delivered","payload":{"messageId":"synthetic-α"}}';vectors=[]
for offset,ts_mode,sig_mode,body_mode in itertools.product((0,-300,300,-301,301),('plain','space'),('plain','upper','bad'),('plain','tamper')):
 ts=str(now+offset);ts=' '+ts+' ' if ts_mode=='space' else ts;sig=hmac.new(secret.encode(),(raw+ts.strip()).encode(),hashlib.sha256).hexdigest();sig=' '+sig.upper()+' ' if sig_mode=='upper' else ('f'*64 if sig_mode=='bad' else sig);body=raw+' ' if body_mode=='tamper' else raw
 with patch('time.time',return_value=now):expected=ns['validate_webhook_signature'](body.encode(),sig,ts,secret)
 vectors.append(dict(raw=body,signature=sig,timestamp=ts,expected=expected))
async def snap(db):
 out={}
 for model,fields in [('Message',['id','status','sent_at','delivered_at','failed_at']),('Contact',['id','is_undeliverable','undeliverable_reason','delivery_fail_count']),('Campaign',['id','messages_delivered','messages_failed']),('CampaignContact',['id','status'])]:out[ns[model].__tablename__]=[{k:cg.dump(getattr(r,k)) for k in fields} for r in (await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all()]
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={col.name:cg.dump(getattr(row,col.name)) for col in row.__table__.columns}));return row
  for i,(old,new) in enumerate(itertools.product(('queued','sending','sent','retrying'),('sent','delivered','failed','cancelled')),1):
   await seed('Contact',first_name='Synthetic '+str(i));await seed('Campaign',name='Receipt '+str(i));await seed('Conversation',contact_id=i);await seed('CampaignContact',contact_id=i,campaign_id=i,status='sent' if i%2 else 'queued');await seed('Message',contact_id=i,conversation_id=i,campaign_id=i,direction='outgoing',channel='sms',provider='smsgate',provider_message_id='receipt-'+str(i),idempotency_key='source-receipt-'+str(i),status=old,body='Synthetic')
   ops.append(dict(pid='receipt-'+str(i),status=new,when='2026-01-02T12:00:00.123456+00:00'))
  await db.commit()
 for op in ops:
  async with sessions() as db:
   await ns['ReceiptReference'](db).process_delivery_status(op['pid'],op['status'],datetime.datetime.fromisoformat(op['when']));await db.commit();op['snapshot']=await snap(db)
 # Repeated multipart delivery and late sent events agree with the original and must not increment again.
 for state in ('delivered','sent','delivered'):
  async with sessions() as db:
   op=dict(pid='receipt-2',status=state,when='2026-01-03T12:00:00+00:00');await ns['ReceiptReference'](db).process_delivery_status(op['pid'],state,datetime.datetime.fromisoformat(op['when']));await db.commit();op['snapshot']=await snap(db);ops.append(op)
 (D/'tests/sms-receipts-source-fixtures.json').write_text(json.dumps(dict(secret=secret,now=now,signatures=vectors,seeds=seeds,operations=ops),indent=2)+'\n');await engine.dispose()
asyncio.run(main())
