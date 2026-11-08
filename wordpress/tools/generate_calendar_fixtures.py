#!/usr/bin/env python3
"""Selected actual calendar source, fixed clock, synthetic SQLite; no app/provider imports or delivery."""
import ast,asyncio,datetime,importlib.util,json,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_calendar_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
from sqlalchemy.orm import relationship,foreign
ns.update(json=json,timedelta=datetime.timedelta,_NameLike=object)
cg.load('backend/app/models/meeting.py',{'Meeting','MeetingAttendee'},models=True)
cg.load('backend/app/models/template.py',{'Template'},models=True)
ns['Meeting'].attendees=relationship(ns['MeetingAttendee'],primaryjoin=ns['Meeting'].id==foreign(ns['MeetingAttendee'].meeting_id),cascade='all, delete-orphan',lazy='selectin')
for name in ('Meeting','MeetingAttendee','Conversation'):ns[name].__table__.dialect_options['sqlite']['autoincrement']=True
for path,names in [('backend/app/models/meeting.py',{'MEETING_TYPES','MEETING_STATUSES'}),('backend/app/services/meeting_service.py',{'_PROTECTED_LEAD_STATUSES','DEFAULT_INVITE_BODY','DEFAULT_REMINDER_BODY'})]:
 nodes=[n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in names for t in n.targets)];exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-calendar-constants','exec'),ns)
ns['datetime']=datetime.datetime;cg.load('backend/app/api/v1/calendar.py',{'MeetingCreate','MeetingUpdate'});ns['datetime']=cg.Clock
cg.load('backend/app/utils/naming.py',{'contact_display_name'})
cg.load('backend/app/services/meeting_service.py',{'_parse_json_list','_parse_json_dict','_as_utc','meeting_tags','reminder_minutes','reminders_sent','meeting_placeholders','serialize_meeting','_contacts_map','_meeting_attendees','_sync_attendees','refresh_meeting','_touch_contact_for_booking','ensure_conversation'})
# Route and service share a name in the originals; preserve the imported serializer helper binding.
ns['calendar_meeting_tags']=ns['meeting_tags']
route_ns=dict(ns)
cg.load('backend/app/api/v1/calendar.py',{'_clean_tags','_clean_minutes','_require_utc','_get_meeting','_parse_iso','list_meetings','upcoming_meetings','day_agenda','meeting_tags','get_meeting','create_meeting','update_meeting','delete_meeting','_set_status'},route_ns)
# SQLite drops timezone information even for source DateTime(timezone=True). Reattach UTC
# on result reads to exercise source arithmetic as on a timezone-aware DB, not to certify MySQL.
class SQLiteUTC(cg.sa.TypeDecorator):
 impl=cg.sa.DateTime;cache_ok=True
 def process_result_value(self,value,dialect):return value.replace(tzinfo=datetime.timezone.utc) if value is not None else None
for col in ns['Meeting'].__table__.columns:
 if isinstance(col.type,cg.sa.DateTime):col.type=SQLiteUTC()
ops=[]
def add(kind,**kw):ops.append(dict(kind=kind,**kw))
def booking(**kw):return dict(dict(title=' Review ',starts_at='2026-01-03T14:00:00+01:00',send_invite_sms=False,send_sms_reminder=False),**kw)
add('list');add('create',body=booking(contact_ids=[1,1,404],tags=[' vip ','vip','VIP','', 'x'*101],location=' Abuja '));add('get',id=1);add('list',q={'contact_id':1});add('tags');add('day',date='2026-01-03');add('upcoming');add('update',id=1,body={'starts_at':'2026-01-04T08:30:00Z'});add('get',id=1)
add('update',id=1,body={'duration_minutes':45,'tags':['Follow-up'],'reminder_minutes':[15,60,15,0,10081]});add('status',id=1,status='confirmed');add('status',id=1,status='completed',notes=' Agreed ');add('get',id=1)
add('create',body=booking(contact_id=2,description='Customer',event_type='call'));add('create',body=booking(conversation_id=1,ends_at='2026-01-03T15:00:00Z'));add('list',q={'search':'Rev%','per_page':1,'page':2});add('list',q={'status':'completed'});add('list',q={'event_type':'call'});add('list',q={'tag':'Follow-up'});add('list',q={'date_from':'invalid','date_to':'2026-01-03T23:59:00Z'});add('status',id=2,status='cancelled',notes='');add('status',id=3,status='no_show');add('upcoming');add('tags');add('day',date='invalid')
add('update',id=2,body={'title':'   '});add('update',id=2,body={'ends_at':'2026-01-02T00:00:00Z'});add('create',body=booking(conversation_id=404));add('create',body=booking(event_type='invalid'));add('get',id=404);add('delete',id=3);add('get',id=3);add('delete',id=404);add('create',body=booking(contact_ids=[3],all_day=True));add('update',id=4,body={'contact_ids':[]});add('get',id=4);add('delete',id=4);add('list')
# Source accepts a whitespace-only create title; native deliberately refuses it, tested separately.
dtos=[]
for update in (False,True):
 for body in [None,[],{},booking(),{'title':'X'},booking(duration_minutes=4),booking(duration_minutes='30'),booking(all_day='yes'),booking(starts_at='bad'),booking(contact_ids=['1',2]),booking(tags=[1]),booking(ends_at=None),{'title':None},{'tags':None},{'event_type':None}]:
  try:d=ns['MeetingUpdate' if update else 'MeetingCreate'].model_validate(body);dtos.append(dict(update=update,input=body,ok=True))
  except cg.ValidationError:dtos.append(dict(update=update,input=body,ok=False))
async def snapshot(db):
 out={}
 for key,model,fields in [('meetings','Meeting',['id','title','description','event_type','status','starts_at','ends_at','all_day','contact_id','conversation_id','campaign_id','tags_json','send_invite_sms','send_invite_email','invite_sent','invite_email_sent','send_sms_reminder','send_email_reminder','reminder_minutes_json','outcome_notes']),('attendees','MeetingAttendee',['id','meeting_id','contact_id','status','notified']),('contacts','Contact',['id','lead_status']),('conversations','Conversation',['id','contact_id','channel','status'])]:
  out[key]=[]
  for row in (await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all():
   item={f:cg.dump(getattr(row,f)) for f in fields}
   for f in list(item):
    if f.endswith('_json'):item[f]=json.loads(item[f]) if item[f] else None
    if f in ['all_day','send_invite_sms','send_invite_email','invite_sent','invite_email_sent','send_sms_reminder','send_email_reminder','notified']:item[f]=int(item[f])
   out[key].append(item)
 return out
helpers=[]
for raw in ['2026-01-03T00:00:00Z','2026-01-03T12:00:00Z','2026-12-31T23:45:00-03:30']:
 for location in [None,'Abuja']:
  row=dict(title='Call é',starts_at=raw,location=location,meeting_link='https://example.test/meeting');obj=types.SimpleNamespace(**dict(row,starts_at=datetime.datetime.fromisoformat(raw.replace('Z','+00:00'))));helpers.append(dict(input=row,expected=ns['meeting_placeholders'](obj)))
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for i,(name,status) in enumerate([('Ada','new'),('Bola','customer'),('Chidi','closed')],1):db.add(ns['Contact'](id=i,first_name=name,email=f'calendar{i}@business.ng',lead_status=status))
  await db.commit()
 for op in ops:
  async with sessions() as db:
   try:
    k=op['kind'];kw=dict(db=db,current_user=types.SimpleNamespace(username='operator'));out=None;status=200
    if k=='create':out=await route_ns['create_meeting'](data=ns['MeetingCreate'].model_validate(op['body']),**kw);status=201
    elif k=='update':out=await route_ns['update_meeting'](meeting_id=op['id'],data=ns['MeetingUpdate'].model_validate(op['body']),**kw)
    elif k=='get':out=await route_ns['get_meeting'](meeting_id=op['id'],**kw)
    elif k=='list':out=await route_ns['list_meetings'](page=op.get('q',{}).get('page',1),per_page=op.get('q',{}).get('per_page',100),**{key:v for key,v in op.get('q',{}).items() if key not in ('page','per_page')},**kw)
    elif k=='tags':out=await route_ns['meeting_tags'](**kw)
    elif k=='day':out=await route_ns['day_agenda'](date=op['date'],**kw)
    elif k=='upcoming':out=await route_ns['upcoming_meetings'](limit=5,**kw)
    elif k=='status':out=await route_ns['_set_status'](db,op['id'],op['status'],op.get('notes'))
    elif k=='delete':out=await route_ns['delete_meeting'](meeting_id=op['id'],**kw);status=204
    op['expected']=dict(status=status,body=cg.dump(out));await db.commit()
    if k=='update' and 'contact_ids' in op['body']:
     async with sessions() as fresh:op['fresh_read']=cg.dump(await route_ns['get_meeting'](meeting_id=op['id'],db=fresh,current_user=None))
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   except cg.ValidationError:await db.rollback();op['expected']=dict(status=422)
  async with sessions() as db:op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/calendar-source-fixtures.json').write_text(json.dumps(dict(operations=ops,dto=dtos,helpers=helpers,scope='Actual selected calendar source AST. Isolated SQLite, fixed UTC clock, synthetic contacts, omitted FKs and reconstructed attendee relationship. No source app imports, providers, native WordPress/MySQL/concurrency/browser/delivery proof.'),indent=2,ensure_ascii=False)+'\n');print(len(ops),'calendar source operations;',len(dtos),'DTO cases')
if __name__=='__main__':asyncio.run(main())
