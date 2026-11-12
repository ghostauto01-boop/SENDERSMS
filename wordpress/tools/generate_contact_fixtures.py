#!/usr/bin/env python3
"""Execute selected original AST only with five synthetic SQLite tables and a fixed clock.
No app/config/environment imports, network/provider calls or production databases.
Relationships are omitted: tests exercise explicit joins, not ORM lazy-loading or native DB semantics.
"""
import ast,asyncio,datetime,json,logging,re,sys,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;sys.path.insert(0,str(D/'.cache/python'))
import sqlalchemy as sa
import phonenumbers
from sqlalchemy.ext.asyncio import AsyncSession,create_async_engine,async_sessionmaker
from sqlalchemy.orm import declarative_base,Mapped,mapped_column
from pydantic import BaseModel,Field,field_validator,ValidationError
NOW=datetime.datetime(2026,1,2,12,0,0,123456,tzinfo=datetime.timezone.utc)
class Clock(datetime.datetime):
 @classmethod
 def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
class HTTPException(Exception):
 def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail
Base=declarative_base()
ns=dict(Base=Base,Mapped=Mapped,mapped_column=mapped_column,datetime=Clock,timezone=datetime.timezone,Optional=typing.Optional,Literal=typing.Literal,BaseModel=BaseModel,Field=Field,field_validator=field_validator,HTTPException=HTTPException,AsyncSession=AsyncSession,User=object,select=sa.select,func=sa.func,or_=sa.or_,sa_delete=sa.delete,sa_update=sa.update,re=re,phonenumbers=phonenumbers,PhoneNumberFormat=phonenumbers.PhoneNumberFormat,NumberParseException=phonenumbers.NumberParseException,NIGERIA_COUNTRY_CODE='234',logger=logging.getLogger('offline-reference'),Depends=lambda *a,**k:None,Query=lambda default=None,**k:default,Body=lambda default=None,**k:default,get_db=lambda:None,get_current_user=lambda:None)
for name in ('Boolean','DateTime','Integer','String','Text','ForeignKey','UniqueConstraint'):ns[name]=getattr(sa,name)
class StripLocalAppImports(ast.NodeTransformer):
 def visit_ImportFrom(self,n):return None if n.module and n.module.startswith('app.') else n

def load(path,names,namespace=ns,models=False):
 nodes=[]
 for n in ast.parse((ROOT/path).read_text()).body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name in names:
   n.decorator_list=[] if not isinstance(n,ast.ClassDef) else n.decorator_list
   if models:n.body=[x for x in n.body if not(isinstance(x,ast.AnnAssign) and isinstance(x.value,ast.Call) and getattr(x.value.func,'id',None)=='relationship')]
   n=StripLocalAppImports().visit(n);nodes.append(n)
 exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-'+path,'exec'),namespace)
load('backend/app/utils/phone.py',{'clean_phone_number','normalize_nigerian_number','phone_search_variants'})
ns['_EMAIL_RE']=re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
load('backend/app/utils/contact_identity.py',{'normalize_email','clean_phone'})
load('backend/app/models/contact.py',{'Contact','Tag','ContactTag'},models=True)
load('backend/app/models/contact_list.py',{'ContactList','ContactListMember'},models=True)
dto_ns=dict(ns,datetime=datetime.datetime)
load('backend/app/schemas/contact.py',{'_tag_list','ContactCreate','ContactUpdate','ContactOut','ContactListOut'},dto_ns)
for name in ('ContactCreate','ContactUpdate','ContactOut','ContactListOut'):ns[name]=dto_ns[name]
load('backend/app/api/v1/contacts.py',{'_set_contact_tags','_tag_names','_serialize_contact','_apply_contact_filters','_apply_channel_filters','list_contacts','list_tags','get_contact','create_contact','update_contact'})
load('backend/app/api/v1/lists.py',{'ListContactsAction','AddMatchingContacts','_find_list','_sync_contact_count','_matching_member_ids','list_lists','find_list_by_name','require_free_list_name','create_list','update_list','get_list_contacts','add_contacts_to_list','_insert_members','_chunked_ids','add_all_matching_contacts','remove_contacts_from_list','get_list_stats'})
async def safe_delete(db,statement):return await db.execute(statement)
ns['_safe_delete']=safe_delete

def dump(x):
 if isinstance(x,BaseModel):return x.model_dump(mode='json')
 if isinstance(x,datetime.datetime):return x.isoformat()
 if isinstance(x,dict):return {k:dump(v) for k,v in x.items()}
 if isinstance(x,(list,tuple)):return [dump(v) for v in x]
 return x
ops=[]
def add(op,**kw):ops.append(dict(op=op,**kw))
add('create',body={'first_name':'Ada','phone_number':'08031112222','tags':' VIP, vip, Buyer , ,0','country':'0'})
add('create',body={'first_name':'Bola','email':'<BOLA@business.ng>,','city':'Abuja'})
add('create',body={});add('create',body={'phone_number':'0','email':'third@business.ng'})
add('create',body={'phone_number':'+2348031112222'});add('create',body={'email':'bola@business.ng'});add('create',body={'email':'bad'})
add('get',id=404);add('get',id=1);add('list',filters={});add('list',filters={'search':'0803111'});add('list',filters={'channel':'email'});add('list',filters={'email_state':'no_email'});add('list',filters={},page=1,per_page=1);add('list',filters={},page=2,per_page=1);add('list',filters={'sort_by':'name); DROP TABLE contacts;--','sort_dir':'asc'})
add('update',id=1,body={'tags':['New','VIP'],'tag_operation':'add','notes':'Test notes'})
add('update',id=1,body={'tags':'vip','tag_operation':'remove'});add('tags');add('update',id=1,body={'tags':None});add('update',id=1,body={'email':'first@business.ng'});add('update',id=1,body={'phone_number':None});add('update',id=1,body={'email':None});add('get',id=1);add('update',id=1,body={'email':'bola@business.ng'});add('update',id=2,body={'first_name':'Bola renamed','tags':['Team']})
add('list_create',name='  Prospects  ',description='Test list');add('list_create',name='prospects');add('list_create',name=' \u3000 ');add('list_create',name='Other');add('list_update',id=2,name='PROSPECTS');add('list_update',id=2,name='Renamed',description='Edited')
add('members_add',id=1,ids=[1,2,2]);add('members_add',id=1,ids=[1,2]);add('members_add',id=1,ids=[2,404]);add('members',id=1);add('stats',id=1);add('lists');add('list',filters={'list_id':1});add('list',filters={'exclude_list_id':1});add('members_remove',id=1,body={'contact_ids':[1]});add('members_remove',id=1,body={'contact_ids':[1]});add('members_add_all',id=1,body={'channel':'email'});add('members_add_all',id=1,body={'channel':'email'});add('members_remove',id=1,body={'scope':'all','search':'Bola'});add('members',id=1);add('stats',id=1);add('members_add',id=404,ids=[])
async def main():
 engine=create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(Base.metadata.create_all)
 sessions=async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   try:
    action=op['op'];body=op.get('body');id=op.get('id');kw=dict(db=db,current_user=None);out=None;status=200
    if action=='create':out=await ns['create_contact'](data=ns['ContactCreate'].model_validate(body,from_attributes=True),**kw);status=201
    elif action=='update':out=await ns['update_contact'](contact_id=id,data=ns['ContactUpdate'].model_validate(body,from_attributes=True),**kw)
    elif action=='get':out=await ns['get_contact'](contact_id=id,**kw)
    elif action=='list':out=await ns['list_contacts'](page=op.get('page',1),per_page=op.get('per_page',25),**op['filters'],**kw)
    elif action=='tags':out=await ns['list_tags'](**kw)
    elif action=='list_create':out=await ns['create_list'](name=op['name'],description=op.get('description'),**kw);status=201
    elif action=='list_update':out=await ns['update_list'](list_id=id,name=op.get('name'),description=op.get('description'),**kw)
    elif action=='lists':out=await ns['list_lists'](**kw)
    elif action=='members_add':out=await ns['add_contacts_to_list'](list_id=id,contact_ids=op['ids'],**kw)
    elif action=='members':out=await ns['get_list_contacts'](list_id=id,**kw)
    elif action=='stats':out=await ns['get_list_stats'](list_id=id,**kw)
    elif action=='members_remove':out=await ns['remove_contacts_from_list'](list_id=id,data=ns['ListContactsAction'].model_validate(body),**kw)
    elif action=='members_add_all':out=await ns['add_all_matching_contacts'](list_id=id,data=ns['AddMatchingContacts'].model_validate(body),**kw)
    await db.commit();op['expected']={'status':status,'body':dump(out)}
   except HTTPException as e:
    await db.rollback();op['expected']={'status':e.status_code,'message':e.detail['message'] if isinstance(e.detail,dict) else e.detail}
   except ValidationError as e:
    await db.rollback();error=e.errors()[0];op['expected']={'status':422,'message':error['msg'],'field':'.'.join(map(str,error['loc'])) or None}
   op['expected_contact_count']=(await db.execute(sa.select(sa.func.count()).select_from(ns['Contact']))).scalar()
 await engine.dispose()
 dto=[]
 values=[None,[],{}, {'phone_number':5},{'phone_number':'x'*21},{'country':'é'*101},{'email':'bad'},{'email':'<A@business.ng>,'},{'lead_status':None},{'tags':False},{'tags':[4]},{'tags':['x'*101]},{'tags':' A, a, \u3000, 0'},{'tags':None},{'tag_operation':'bad'},{'has_consented':'yes'},{'is_opted_out':0},{'custom_fields':{'a':1}},{'unknown':'ignored'}]
 for update in (False,True):
  for value in values:
   cls=ns['ContactUpdate' if update else 'ContactCreate']
   try:expected={'body':cls.model_validate(value,from_attributes=True).model_dump(exclude_unset=update)}
   except ValidationError as e:
    error=e.errors()[0];expected={'message':error['msg'],'field':'.'.join(map(str,error['loc'])) or None}
   dto.append({'update':update,'input':value,'expected':expected})
 (D/'tests/contact-source-fixtures.json').write_text(json.dumps({'operations':ops,'dto':dto,'scope':'Original selected AST only; five synthetic SQLite tables, omitted relationships and fixed clock; not HTTP, provider, native DB, Unicode-collation or concurrency proof'},ensure_ascii=False,indent=2)+'\n')
 print(len(ops),'source operations;',len(dto),'source DTO cases')
if __name__=='__main__':asyncio.run(main())
