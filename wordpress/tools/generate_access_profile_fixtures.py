#!/usr/bin/env python3
"""Selected source AST and fixed-clock SQLite only. Synthetic hash codec, no login/provider/native proof."""
import asyncio,datetime,hashlib,importlib.util,json
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('access_profile_reference',D/'tools/generate_variable_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
ns['AUTH_PASSWORD_REQUIRED']='auth.password_required';ns['AUTH_PASSWORD_HASH']='auth.password_hash';ns['hash_password']=lambda x:'synthetic:'+hashlib.sha256(x.encode()).hexdigest()
cg.load('backend/app/models/system.py',{'SystemSetting'},models=True)
cg.load('backend/app/services/system_settings.py',{'get_setting','set_setting','_is_truthy','is_site_password_required','get_site_password_hash','get_site_access','set_site_access'})
cg.load('backend/app/schemas/auth.py',{'SiteAccessUpdate'})
cg.load('backend/app/api/v1/variables.py',{'contact_profile'})
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);access=[]
 for initial,body in [(None,None),(None,{'password_required':True}),(None,{'password_required':True,'password':'abc'}),(None,{'password_required':True,'password':'x'*129}),(None,{'password_required':True,'password':'  source-pass  '}),('source-pass',{'password_required':True}),('source-pass',{'password_required':True,'password':'rotated-pass'}),('source-pass',{'password_required':False})]:
  async with sessions() as db:
   if initial:await ns['set_site_access'](db,password_required=True,password=initial)
   try:
    out=await ns['get_site_access'](db) if body is None else await ns['set_site_access'](db,**body);expected={'status':200,'body':out}
   except ValueError:expected={'status':400}
   rows=(await db.execute(cg.sa.select(ns['SystemSetting']).order_by(ns['SystemSetting'].key))).scalars().all();state={r.key:r.value for r in rows}
   access.append(dict(initial=initial,body=body,source_expected=expected,source_state=state,native_divergence=body is not None and body['password_required'] is False));await db.rollback()
 dto=[]
 for body in [{},{'password_required':None},{'password_required':'yes'},{'password_required':False,'password':None},{'password_required':True,'password':1},{'password_required':True,'password':'abc'},{'password_required':False,'extra':'ignored'}]:
  try:v=ns['SiteAccessUpdate'].model_validate(body);dto.append(dict(input=body,ok=True,expected=v.model_dump()))
  except cg.ValidationError:dto.append(dict(input=body,ok=False))
 seeds=[];profiles=[]
 async with sessions() as db:
  async def seed(model,**values):
   r=ns[model](**values);db.add(r);await db.flush();seeds.append(dict(table=r.__tablename__,values={c.name:cg.dump(getattr(r,c.name)) for c in r.__table__.columns}));return r
  custom={'Project Name':'Skyline','flag':False,'yes':True,'amount':1.0,'empty':None,'0':'zero','list':['a',False,None],'object':{'x':'Ada'},'State':'Custom FCT','notes':'Imported note'}
  await seed('Contact',first_name='Ada',last_name='Obi',phone_number='+2348031112222',email='ada@business.ng',custom_fields=json.dumps(custom),notes='Private note',messages_sent=4,messages_received=2,last_contacted_at=cg.NOW,last_reply_at=cg.NOW)
  await seed('Contact',business_name='Bola Ltd',email='bola@business.ng',custom_fields='{"Only Second":"not discovered by first-contact sync"}');await seed('Contact',email='broken@business.ng',custom_fields='{bad');await seed('Contact',custom_fields='[]');await seed('Contact',first_name=' ',last_name=' Name ',custom_fields='{}')
  await seed('Tag',name='VIP');await seed('Tag',name='Buyer');await seed('ContactTag',contact_id=1,tag_id=1);await seed('ContactTag',contact_id=1,tag_id=2)
  await seed('ContactVariable',field_key='project_name',label='Project label',shortcode='project',is_active=False,source='imported')
  await db.commit()
 for id in (1,2,3,4,5,999,1):
  async with sessions() as db:
   try:out=await ns['contact_profile'](id,db=db,current_user=None);await db.commit();expected=dict(status=200,body=cg.dump(out))
   except cg.HTTPException as e:await db.rollback();expected=dict(status=e.status_code)
   variables=(await db.execute(cg.sa.select(ns['ContactVariable']).order_by(ns['ContactVariable'].id))).scalars().all();state=[{k:cg.dump(getattr(v,k)) for k in ('field_key','label','shortcode','source','is_active','contact_count','sample_value')} for v in variables];profiles.append(dict(id=id,expected=expected,variables=state))
 (D/'tests/access-profile-source-fixtures.json').write_text(json.dumps(dict(access=access,dto=dto,seeds=seeds,profiles=profiles,scope='Selected source, fixed clock/SQLite and synthetic SHA hash codec; no production crypto, HTTP, concurrency or browser proof'),indent=2,ensure_ascii=False)+'\n');await engine.dispose()
asyncio.run(main())
