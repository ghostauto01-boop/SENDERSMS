#!/usr/bin/env python3
"""Actual selected dashboard/metrics AST, fixed-clock synthetic SQLite; no gateway/worker/provider calls."""
import asyncio,datetime,importlib.util,json,types
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dashboard_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
ns.update(Float=cg.sa.Float,settings=types.SimpleNamespace(DEFAULT_TIMEZONE='Africa/Lagos'),ZoneInfo=ZoneInfo,ZoneInfoNotFoundError=ZoneInfoNotFoundError,SENDING_ADS=('active',),SENDING_CLASSIC=('running',))
cg.load('backend/app/utils/datetime.py',{'local_day_utc_bounds'});cg.load('backend/app/models/ads.py',{'_now'});cg.load('backend/app/models/ads.py',{'AdsCampaign'},models=True);cg.load('backend/app/api/v1/dashboard.py',{'_channel_filter','get_dashboard_stats','get_dashboard_charts'})
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  by_table={m.__tablename__:m for m in (ns[n] for n in ('Contact','Campaign','Conversation','Message','FollowUp','AdsCampaign','CallLog'))}
  fixture=json.loads((D/'tests/analytics-source-fixtures.json').read_text())
  for seed in fixture['seeds']:
   model=by_table[seed['table']];values=seed['values'].copy()
   for k,v in values.items():
    if v is not None and isinstance(model.__table__.columns[k].type,cg.sa.DateTime):values[k]=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
   db.add(model(**values));seeds.append(seed)
  await db.flush()
  async def add(model,**v):
   r=ns[model](**v);db.add(r);await db.flush();seeds.append(dict(table=r.__tablename__,values={c.name:cg.dump(getattr(r,c.name)) for c in r.__table__.columns}));return r
  for channel in ('sms','email'):
   for status in ('active','completed','paused'):await add('AdsCampaign',name='Stored '+channel+status,channel=channel,status=status)
  await add('Campaign',name='Classic running',status='running',channel='sms',updated_at=cg.NOW+datetime.timedelta(minutes=1));await add('Campaign',name='Classic completed',status='completed',channel='email',updated_at=cg.NOW+datetime.timedelta(minutes=2))
  for i in (4,5,6):await add('Conversation',contact_id=i,channel='email' if i==5 else 'sms',last_message_at=cg.NOW-datetime.timedelta(hours=i),last_message_preview='Stored preview',unread_count=i)
  start,end=ns['local_day_utc_bounds']()
  for i,t in enumerate((start-datetime.timedelta(microseconds=1),start,end-datetime.timedelta(microseconds=1),end)):await add('FollowUp',contact_id=1,scheduled_at=t,channel='sms' if i%2 else 'email',message_text='Stored')
  await db.commit()
 for kind in ('stats','charts'):
  for channel in (None,'sms','email','all'):
   for days in ((None,1,3) if kind=='stats' else (1,3,30)):
    async with sessions() as db:
     out=await ns['get_dashboard_stats' if kind=='stats' else 'get_dashboard_charts'](channel=channel,days=days,db=db,current_user=None);ops.append(dict(kind=kind,channel=channel,days=days,source_expected=cg.dump(out)))
 (D/'tests/dashboard-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,scope='Actual selected source dashboard/metrics, fixed clock and synthetic SQLite; native gateway status intentionally not_verified, not fabricated configured'),indent=2)+'\n');await engine.dispose()
asyncio.run(main())
