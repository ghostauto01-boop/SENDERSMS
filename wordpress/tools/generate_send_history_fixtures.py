#!/usr/bin/env python3
"""Selected actual source history and channel parser; synthetic SQLite, no app imports or sends."""
import asyncio,datetime,importlib.util,itertools,json
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('history_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
cg.load('backend/app/api/v1/send.py',{'get_history'})
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={col.name:cg.dump(getattr(row,col.name)) for col in row.__table__.columns}));return row
  for v in (dict(first_name='Ada',last_name='Obi',phone_number='+2348031234567'),dict(first_name=' ',business_name='Shop',phone_number='+2348032234567'),dict(business_name='0',email='zero@example.invalid'),dict(phone_number='+2348033234567'),dict(email='no-name@example.invalid')):await seed('Contact',**v)
  for i,(channel,direction,status) in enumerate(itertools.product(('sms','email'),('outgoing','incoming'),('sent','delivered','failed','cancelled','queued','sending','unknown'))):
   await seed('Message',idempotency_key='history-'+str(i),conversation_id=1,contact_id=i%5+1,channel=channel,direction=direction,status=status,body='History body '+str(i) if i%2 else '😀'*105,created_at=cg.NOW-datetime.timedelta(minutes=i),last_error='PRIVATE secret '+str(i) if status=='failed' else ('' if i%3==0 else None),provider_message_id='provider-'+str(i) if status in ('sent','delivered') else None,provider_response='NEVER EXPOSE provider credentials',sent_at=cg.NOW if status in ('sent','delivered') else None,delivered_at=cg.NOW if status=='delivered' else None,failed_at=cg.NOW if status=='failed' else None)
  # Orphan contact projection is tested without simulating native FK enforcement.
  await seed('Message',idempotency_key='history-legacy',conversation_id=1,contact_id=999,channel='sms',direction='outgoing',body='Legacy orphan body',status='sent',created_at=cg.NOW+datetime.timedelta(minutes=1))
  await db.commit()
  queries=[{},dict(channel='all'),dict(channel='email'),dict(channel=' SMS '),dict(channel=''),dict(channel='fax'),dict(channel='all',search='PRIVATE'),dict(search='body'),dict(search='😀'),dict(search='%'),dict(search='_'),dict(search="' OR 1=1 --"),dict(search='not present'),dict(page=99),dict(page=2,per_page=3)]
  for channel,status in itertools.product(('sms','email','all'),('sent','delivered','failed','cancelled','queued','sending','unknown','all','nonexistent')):queries.append(dict(channel=channel,status=status))
  for query in queries:
   try:result=await ns['get_history'](db=db,cu=None,**query);ops.append(dict(query=query,status=200,expected=cg.dump(result)))
   except cg.HTTPException as e:ops.append(dict(query=query,status=e.status_code,expected=e.detail))
 (D/'tests/send-history-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,scope='Selected actual source history and channel parser AST only; fixed-clock synthetic SQLite without FK/relationship/native concurrency/HTTP/provider certification.'),indent=2,ensure_ascii=False)+'\n');await engine.dispose()
asyncio.run(main())
