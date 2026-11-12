#!/usr/bin/env python3
"""Actual selected source scheduled reads/cancel/date parser; fixed-clock SQLite, no network/app imports."""
import ast,asyncio,datetime,importlib.util,json
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scheduled_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg);ns=cg.ns
# Relationships and native FK/race behavior are explicitly not simulated by this oracle.
def visit_call(self,node):
 self.generic_visit(node)
 if getattr(node.func,'id','')=='mapped_column':node.args=[a for a in node.args if not(isinstance(a,ast.Call) and getattr(a.func,'id','')=='ForeignKey')]
 return node
cg.StripLocalAppImports.visit_Call=visit_call
cg.load('backend/app/models/scheduled.py',{'ScheduledMessage'},models=True)
cg.load('backend/app/api/v1/send.py',{'_parse_schedule_at','get_scheduled','cancel_scheduled'})
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[];dates=[]
 async with sessions() as db:
  async def add(model,**v):
   row=ns[model](**v);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={col.name:cg.dump(getattr(row,col.name)) for col in row.__table__.columns}));return row
  await add('Contact',first_name='Ada',last_name='Obi',phone_number='+2348031234567')
  await add('Contact',first_name=' ',business_name='Shop',phone_number='+2348032234567')
  for i,status in enumerate(('pending','sent','failed','cancelled','pending','pending')):
   await add('ScheduledMessage',contact_id=1 if i%2==0 else (2 if i==1 else None),phone_number='+2348031234567' if i%2==0 else None,channel='email' if i==5 else 'sms',body='Hello scheduled '+str(i) if i!=4 else '😀'*130,schedule_at=cg.NOW+datetime.timedelta(hours=i+1),status=status,error='private-provider-reason' if status=='failed' else None)
  await db.commit()
  for q in ({},{'status':'pending'},{'status':'all'},{'status':'failed'},{'search':'SCHEDULED'},{'search':'%'},{'search':'😀'},{'page':2,'per_page':2},{'page':99}):
   out=await ns['get_scheduled'](db=db,cu=None,**q);ops.append(dict(kind='list',query=q,expected=cg.dump(out)))
  for id in (1,1,2,9999,6):
   try:out=await ns['cancel_scheduled'](sid=id,db=db,cu=None);await db.commit();ops.append(dict(kind='cancel',id=id,status=200,expected=cg.dump(out)))
   except cg.HTTPException as e:await db.rollback();ops.append(dict(kind='cancel',id=id,status=e.status_code,expected=e.detail))
  out=await ns['get_scheduled'](db=db,cu=None);ops.append(dict(kind='list',query={},expected=cg.dump(out)))
 for raw in ('2026-01-03T09:00:00+01:00','2026-01-03T08:00:00Z','2026-01-03 08:00:00','2026-01-02T12:00:00.123456Z','2026-01-02T12:00:00.123457Z','bad'):
  try:dates.append(dict(input=raw,status=200,expected=cg.dump(ns['_parse_schedule_at'](raw))))
  except cg.HTTPException as e:dates.append(dict(input=raw,status=e.status_code,expected=e.detail))
 (D/'tests/scheduled-sms-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,dates=dates,scope='Actual selected source scheduled listing/cancel/date parser; fixed-clock synthetic SQLite, no ORM relationships/native FKs, HTTP, scheduling, concurrency or SMS evidence.'),indent=2,ensure_ascii=False)+'\n');await engine.dispose()
asyncio.run(main())
