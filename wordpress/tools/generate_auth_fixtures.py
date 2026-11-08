#!/usr/bin/env python3
"""Original auth business/DTO AST, two synthetic SQLite models, synthetic password codec only.
No original app/config imports, environments, credentials, providers or live databases.
"""
import ast,asyncio,datetime,hmac,json,socket,sys,types
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;sys.path.insert(0,str(D/'.cache/python'))
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession,create_async_engine,async_sessionmaker
from sqlalchemy.orm import declarative_base,Mapped,mapped_column
from pydantic import BaseModel,Field,ValidationError
NOW=datetime.datetime(2026,1,2,12,0,0,123456,tzinfo=datetime.timezone.utc)
class Clock(datetime.datetime):
 @classmethod
 def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
Base=declarative_base();ns=dict(Base=Base,Mapped=Mapped,mapped_column=mapped_column,Boolean=sa.Boolean,DateTime=sa.DateTime,Integer=sa.Integer,String=sa.String,Text=sa.Text,datetime=Clock,timezone=datetime.timezone,AsyncSession=AsyncSession,select=sa.select,IntegrityError=sa.exc.IntegrityError,hmac=hmac)
for path,cls in [('backend/app/models/user.py','User'),('backend/app/models/system.py','SystemSetting')]:
 node=next(n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.ClassDef) and n.name==cls);exec(compile(ast.Module(body=[node],type_ignores=[]),'source-'+path,'exec'),ns)
class FakeCodec:
 def hash(self,value):return 'synthetic:'+value
 def verify(self,stored,value):return stored=='synthetic:'+value
class CodecError(Exception):pass
ns.update(ph=FakeCodec(),VerifyMismatchError=CodecError,VerificationError=CodecError,InvalidHashError=CodecError)
for path,names in [('backend/app/security/auth.py',{'hash_password','verify_password','ensure_admin'}),('backend/app/api/v1/auth.py',{'configured_passwords','password_required','check_password','_authenticate','access_status'})]:
 nodes=[]
 for n in ast.parse((ROOT/path).read_text()).body:
  if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names:n.decorator_list=[];nodes.append(n)
 # Dependencies are type-only or substituted below; none invoke original app modules.
 ns.update(User=ns['User'],Depends=lambda x:None,get_db=lambda:None)
 exec(compile(ast.Module(body=nodes,type_ignores=[]),'source-'+path,'exec'),ns)
async def site(db):
 row=(await db.execute(sa.select(ns['SystemSetting']).where(ns['SystemSetting'].key=='auth.password_hash'))).scalar_one_or_none();return row.value or None if row else None
ns['site_password_hash']=site
cases=[]
def add(name,password,username=None,admin='operator',legacy='',users=None,site=None):cases.append(dict(name=name,password=password,username=username,admin=admin,legacy=legacy,users=users or [],site=site))
add('missing password',None);add('blank password',' \u00a0 ');add('operator first login','operator');add('wrong first login','wrong');add('trimmed operator password','  operator\u00a0');add('unknown username operator fallback','operator','unknown');add('legacy disabled by operator','legacy',admin='operator',legacy='legacy');add('legacy only','legacy',admin='',legacy='legacy');add('explicit no-password deployment',None,admin='',legacy='')
seed={'id':1,'username':'admin','password_hash':'synthetic:old','display_name':'Existing','role':'member','is_active':False,'created_at':'2025-01-01T00:00:00+00:00','updated_at':'2025-01-01T00:00:00+00:00'}
add('admin repaired even on wrong password','wrong',users=[seed]);add('admin repaired and active','operator',users=[seed]);add('site alternative with unknown username','site','unknown',site='synthetic:site');add('site alternative default username','site',site='synthetic:site');add('malformed site hash no empty-password bypass',None,admin='',site='0')
extra={'id':1,'username':'staff','password_hash':'synthetic:staff-password','display_name':'Staff','role':'staff','is_active':True}
add('named account password','staff-password','staff',users=[extra]);add('operator password accepted for active named user','operator','staff',users=[extra]);add('disabled named account own password rejected','staff-password','staff',users=[dict(extra,is_active=False)]);add('operator password disabled named falls back admin','operator','staff',users=[dict(extra,is_active=False)]);add('whitespace username','operator','  admin\u3000');add('spaced configured password source repair quirk','operator',admin=' operator ');add('zero text is a real password','0',admin='0')
async def main():
 for c in cases:
  engine=create_async_engine('sqlite+aiosqlite:///:memory:')
  async with engine.begin() as con:await con.run_sync(Base.metadata.create_all)
  sessions=async_sessionmaker(engine,expire_on_commit=False)
  ns['settings']=types.SimpleNamespace(ADMIN_USERNAME='admin',ADMIN_PASSWORD=c['admin'],LOGIN_PASSWORD=c['legacy'])
  async with sessions() as db:
   for u in c['users']:
    row={k:datetime.datetime.fromisoformat(v) if k in ('created_at','updated_at') else v for k,v in u.items()};db.add(ns['User'](**row))
   if c['site'] is not None:db.add(ns['SystemSetting'](key='auth.password_hash',value=c['site']))
   await db.commit();access=await ns['access_status'](db);user=await ns['_authenticate'](db,c['username'],c['password']);await db.commit()
   c['expected_access']=access;c['expected_user']={k:getattr(user,k) for k in ('id','username','display_name','role','is_active')} if user else None
   rows=(await db.execute(sa.select(ns['User']).order_by(ns['User'].id))).scalars().all();c['expected_users']=[{k:(getattr(u,k).isoformat() if isinstance(getattr(u,k),datetime.datetime) else getattr(u,k)) for k in ('id','username','password_hash','email','display_name','role','is_active','last_login','created_at','updated_at')} for u in rows]
  await engine.dispose()
 dto_ns=dict(BaseModel=BaseModel,Field=Field);node=next(n for n in ast.parse((ROOT/'backend/app/schemas/auth.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='LoginRequest');exec(compile(ast.Module(body=[node],type_ignores=[]),'source-login-dto','exec'),dto_ns)
 dto=[]
 for value in [None,[],1,'text',{}, {'extra':'ignored'},{'username':None,'password':None},{'username':5},{'password':False},{'username':[],'password':5},{'password':'0'},{'username':'\u00a0admin','password':'é'}]:
  for optional in (False,True):
   if value is None:expected={'body':{}} if optional else {'message':'Field required','field':None}
   else:
    try:expected={'body':dto_ns['LoginRequest'].model_validate(value,from_attributes=True).model_dump()}
    except ValidationError as exc:
     err=exc.errors()[0];expected={'message':err['msg'],'field':'.'.join(map(str,err['loc'])) or None}
   dto.append({'input':value,'optional':optional,'expected':expected})
 (D/'tests/auth-source-fixtures.json').write_text(json.dumps({'cases':cases,'dto':dto,'scope':'Original auth/DTO AST, synthetic SQLite and explicitly fake password codec; not native Argon2/login/HTTP proof'},ensure_ascii=False,indent=2)+'\n');print(len(cases),'source auth scenarios;',len(dto),'source DTO scenarios')
if __name__=='__main__':asyncio.run(main())
