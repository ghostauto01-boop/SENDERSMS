#!/usr/bin/env python3
"""Selected actual contact/list lifecycle functions, isolated SQLite and clock. No provider/config/runtime imports."""
import ast,asyncio,dataclasses,importlib.util,json,types,uuid
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('lifecycle_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
ns.update(Index=cg.sa.Index,JSON=cg.sa.JSON,Float=cg.sa.Float,uuid4=uuid.uuid4,_now=lambda:cg.NOW,OperationalError=cg.sa.exc.OperationalError,ProgrammingError=cg.sa.exc.ProgrammingError)
for path,names in [('ads',{'AdsAssignment','AdsFollowUpTask','AdsCalendarEvent','AdsEvent'}),('campaign',{'CampaignContact'}),('campaign_followup',{'CampaignFollowUpLog'}),('meeting',{'Meeting','MeetingAttendee'}),('scheduled',{'ScheduledMessage'}),('suppression',{'SuppressionEntry'}),('email',{'EmailSuppression'})]:cg.load('backend/app/models/'+path+'.py',names,models=True)
cg.load('backend/app/schemas/contact.py',{'BulkAction'})
cg.load('backend/app/api/v1/contacts.py',{'_is_missing_table_error','_safe_delete','_safe_update','_chunked','_refresh_list_counts','_delete_contacts_bulk','_delete_contact_permanently','delete_contact','bulk_action','sms_opt_in','email_opt_in','email_opt_out','get_contact_activity'})
cg.load('backend/app/services/email_service.py',{'get_suppression','suppress_email','unsuppress_email','unsubscribe_contact'})
ns['email_service']=types.SimpleNamespace(**{k:ns[k] for k in ('get_suppression','suppress_email','unsuppress_email','unsubscribe_contact')})
cg.load('backend/app/api/v1/lists.py',{'delete_list','delete_contacts_from_list_permanently'})
ns.update(dataclass=dataclasses.dataclass,field=dataclasses.field)
for path,names in [('backend/app/utils/phone.py',{'NG_MOBILE_PREFIXES'}),('backend/app/services/number_filter.py',{'_EXAMPLE_LOCAL','_SEQUENTIAL_FULL'}),('backend/app/services/quarantine.py',{'GENERATED_TAG','NOT_SENDABLE_TAG','TAGS','ROLE_LOCALS'})]:
 nodes=[n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in names for t in n.targets)]
 exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-hygiene-constants','exec'),ns)
cg.load('backend/app/utils/phone.py',{'is_nigerian_mobile'})
cg.load('backend/app/services/number_filter.py',{'FilterResult','_national10','_is_hard_fake','looks_like_test_data','classify_number'})
ns['_classify']=ns['classify_number']
cg.load('backend/app/services/list_hygiene.py',{'classify_number','mark_undeliverable','clean_contacts'})
cg.load('backend/app/services/quarantine.py',{'_tag','quarantine_generated'})
from sqlalchemy.orm import relationship,foreign
ns['ContactList'].members=relationship(ns['ContactListMember'],primaryjoin=ns['ContactList'].id==foreign(ns['ContactListMember'].list_id),cascade='all, delete-orphan')
for t in cg.Base.metadata.tables.values():t.dialect_options['sqlite']['autoincrement']=True
snapshot_fields={'Contact':['id','email','phone_number','lead_status','is_opted_out','has_consented','consent_status','is_email_opted_out','email_status','email_source','is_undeliverable','undeliverable_reason','delivery_fail_count'],'ContactList':['id','name','contact_count'],'ContactListMember':['list_id','contact_id'],'Tag':['id','name'],'ContactTag':['contact_id','tag_id'],'SuppressionEntry':['phone_number','contact_id'],'EmailSuppression':['email_address','contact_id','source','reason','hard_bounce']}
async def snapshot(db):
 out={}
 for name,fields in snapshot_fields.items():
  rows=(await db.execute(cg.sa.select(ns[name]).order_by(ns[name].id))).scalars().all();items=[{k:getattr(row,k) for k in fields} for row in rows]
  out[ns[name].__tablename__]=sorted(items,key=lambda d:json.dumps(d,sort_keys=True))
 return out
ops=[]
def add(kind,**kwargs):ops.append(dict(kind=kind,**kwargs))
add('sms_in',id=1,body={});add('sms_in',id=1,body={'consent_confirmed':'true'});add('sms_in',id=404,body={'consent_confirmed':True});add('sms_in',id=2,body={'consent_confirmed':True});add('sms_in',id=1,body={'consent_confirmed':True})
add('email_out',id=1,reason='Stop please');add('email_out',id=1,reason='Again');add('email_out',id=404);add('email_in',id=1);add('email_in',id=404)
add('clean',list_id=1);add('clean',list_id=1,remove=True);add('quarantine');add('quarantine',dry_run=False);add('quarantine',dry_run=False)
add('bulk',body=dict(action='status',contact_ids=[1,1,2,404],value='interested'));add('bulk',body=dict(action='tag',contact_ids=[1,2],value='Batch'));add('bulk',body=dict(action='tag',contact_ids=[1,2],value='Batch'));add('bulk',body=dict(action='delete',contact_ids=[]));add('bulk',body=dict(action='delete',contact_ids=[3,3,404]));add('bulk',body=dict(action='status',scope='all',value='new'))
add('member_delete',id=1,body=dict(contact_ids=[4]));add('member_delete',id=1,body=dict(contact_ids=[2,2,4]));add('member_delete',id=404,body=dict(scope='all'));add('member_delete',id=2,body=dict(scope='all',search='missing'));add('activity',id=404)
add('delete',id=404);add('list_delete',id=1);add('bulk',body=dict(action='delete',scope='all',channel='email',list_id=2));add('list_delete',id=2,delete_contacts=True);add('delete',id=4)
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[]
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={c.name:cg.dump(getattr(row,c.name)) for c in row.__table__.columns}));return row
  await seed('Contact',email='first@business.ng',phone_number='+2348031112222',is_opted_out=True,consent_status='opted_out');await seed('Contact',email='second@business.ng');await seed('Contact',phone_number='123');await seed('Contact',email='info@business.ng',email_source='inferred');await seed('Contact',phone_number='+2348123456789');await seed('Contact',phone_number='+2348031111111')
  await seed('ContactList',name='First');await seed('ContactList',name='Second')
  for list_id,contact_id in [(1,1),(1,2),(1,3),(1,5),(1,6),(2,1),(2,3)]:await seed('ContactListMember',list_id=list_id,contact_id=contact_id)
  await seed('SuppressionEntry',contact_id=1,phone_number='+2348031112222',source='manual');await db.commit()
  for op in ops:
   kind=op['kind'];kw=dict(db=db,current_user=None);out=None;status=200
   try:
    if kind=='sms_in':out=await ns['sms_opt_in'](contact_id=op['id'],confirmation=op['body'],**kw)
    elif kind=='email_in':out=await ns['email_opt_in'](contact_id=op['id'],**kw)
    elif kind=='email_out':out=await ns['email_opt_out'](contact_id=op['id'],reason=op.get('reason'),**kw)
    elif kind=='clean':out={'success':True,**await ns['clean_contacts'](db,list_id=op.get('list_id'),remove_from_list=op.get('remove',False),delete_contacts=op.get('delete',False))}
    elif kind=='quarantine':out=await ns['quarantine_generated'](db,dry_run=op.get('dry_run',True))
    elif kind=='bulk':out=await ns['bulk_action'](data=ns['BulkAction'].model_validate(op['body']),**kw)
    elif kind=='member_delete':out=await ns['delete_contacts_from_list_permanently'](list_id=op['id'],data=ns['ListContactsAction'].model_validate(op['body']),**kw)
    elif kind=='delete':out=await ns['delete_contact'](contact_id=op['id'],**kw);status=204
    elif kind=='list_delete':out=await ns['delete_list'](list_id=op['id'],delete_contacts=op.get('delete_contacts',False),**kw);status=204
    elif kind=='activity':out=await ns['get_contact_activity'](contact_id=op['id'],page=1,per_page=20,**kw)
    await db.commit();op['expected']=dict(status=status,body=cg.dump(out))
   except cg.HTTPException as e:
    await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/contact-action-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,fields=snapshot_fields,scope='Actual selected source lifecycle functions; fixed clock, omitted FKs and only one reconstructed ORM cascade. Cases deliberately avoid source destructive shared-calendar and suppression-erasure bugs; native hardening has separate tests. Not native install/HTTP/concurrency/provider/browser proof.'),indent=2,ensure_ascii=False)+'\n');print(len(ops),'source lifecycle operations and state snapshots')
if __name__=='__main__':asyncio.run(main())
