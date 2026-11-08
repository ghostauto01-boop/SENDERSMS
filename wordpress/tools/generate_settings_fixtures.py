#!/usr/bin/env python3
"""Execute selected actual source workspace settings, without app/config/provider imports. Synthetic crypto marker only."""
import ast,asyncio,importlib.util,json
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('settings_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg);ns=cg.ns
cg.load('backend/app/models/system.py',{'SystemSetting'},models=True);cg.load('backend/app/models/notification.py',{'NotificationProvider'},models=True)
for path,names in [('backend/app/api/v1/settings.py',{'APPEARANCE_SETTING_KEY','VALID_PALETTES','VALID_MODES','VALID_DENSITIES','VALID_RADII','VALID_FONTS'}),('backend/app/services/system_settings.py',{'NOTIFY_MUTED_SENDERS','DEFAULT_MUTED_SENDERS'})]:
 nodes=[n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in names for t in n.targets)]
 exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-settings-constants','exec'),ns)
ns.update(json=json,encrypt_value=lambda value:'synthetic-encryption:'+value)
cg.load('backend/app/services/system_settings.py',{'get_setting','set_setting','get_muted_notify_senders','set_muted_notify_senders'})
cg.load('backend/app/api/v1/settings.py',{'AppearanceUpdate','get_appearance','put_appearance','get_comp','put_comp','get_notifs','put_notif','get_muted_senders','put_muted_senders'})
ops=[]
def add(kind,**kw):ops.append(dict(kind=kind,**kw))
add('appearance');add('appearance_put',body={'mode':'dark','palette':'sapphire','font':'mono'});add('appearance');add('appearance_put',body={'density':'compact','radius':'sharp'});add('appearance_put',body={'mode':None,'unknown':'ignored'});add('appearance_put',body={'palette':'unsupported'});add('appearance_put',body={'mode':'DARK'});add('appearance_put',body={'font':'Comic Sans'});add('appearance_put',body={'density':'small'});add('appearance_put',body={'radius':'circle'})
add('compliance');add('compliance_put',query={'ec':True,'es':False});add('compliance');add('compliance_put',query={'ea':False,'ek':True});add('compliance_put',query={});add('compliance')
add('muted');
for raw in (' mtn, AIRTEL,mtn, , 0 ','straße, STRASSE, café, Café, ı, i, ﬁ','', '  Αθηνα, αθηνα, σ,ς,Σ,İ, i ','MTN,\u3000AIRTEL,MTN'):add('muted_put',senders=raw);add('muted')
add('notifications');add('notification_put',provider='invalid',query={});add('notification_put',provider='pushover',query={'user_key':'synthetic-user','app_token':'synthetic-token','is_enabled':True});add('notifications');add('notification_put',provider='pushover',query={'user_key':'replacement'});add('notifications');add('notification_put',provider='pushover',query={'app_token':''});add('notification_put',provider='pushover',query={'is_enabled':False});add('notifications')
async def snapshot(db):
 settings=(await db.execute(cg.sa.select(ns['SystemSetting']).order_by(ns['SystemSetting'].id))).scalars().all();providers=(await db.execute(cg.sa.select(ns['NotificationProvider']).order_by(ns['NotificationProvider'].id))).scalars().all()
 return dict(settings=[dict(key=r.key,value=json.loads(r.value) if r.key=='appearance.preferences' else r.value,category=r.category,description=r.description) for r in settings],providers=[dict(provider=p.provider,is_enabled=p.is_enabled,credentials={k.removesuffix('_encrypted'):v.removeprefix('synthetic-encryption:') for k,v in json.loads(p.config_json or '{}').items()}) for p in providers])
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   kw=dict(db=db,cu=None)
   try:
    k=op['kind']
    if k=='appearance':body=await ns['get_appearance'](**kw)
    elif k=='appearance_put':body=await ns['put_appearance'](body=ns['AppearanceUpdate'].model_validate(op['body']),**kw)
    elif k=='compliance':body=await ns['get_comp'](**kw)
    elif k=='compliance_put':body=await ns['put_comp'](**op['query'],**kw)
    elif k=='muted':body=await ns['get_muted_senders'](**kw)
    elif k=='muted_put':body=await ns['put_muted_senders'](senders=op['senders'],**kw)
    elif k=='notifications':body=await ns['get_notifs'](**kw)
    elif k=='notification_put':body=await ns['put_notif'](provider=op['provider'],**op['query'],**kw)
    await db.commit();op['expected']=dict(status=200,body=cg.dump(body))
   except cg.HTTPException as e:
    await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   op['snapshot']=await snapshot(db)
 dto=[]
 for body in (None,[],{}, {'mode':None},{'mode':1},{'mode':False},{'mode':[]},{'palette':'emerald'},{'density':{}},{'font_scale':'large'},{'unknown':'ignored'}):
  try:out=ns['AppearanceUpdate'].model_validate(body).model_dump(exclude_unset=True);dto.append(dict(input=body,ok=True,expected=out))
  except cg.ValidationError:dto.append(dict(input=body,ok=False))
 await engine.dispose()
 (D/'tests/settings-source-fixtures.json').write_text(json.dumps(dict(operations=ops,dto=dto,scope='Selected actual original settings/schema/service functions, synthetic SQLite and clock. Synthetic source encryption marker, not Fernet/AES equivalence. Native credential envelopes, admin writes, malformed stored preferences, JSON-not-query secrets, aliases and resource guards are separate intentional safety tests. No real WordPress/MySQL/provider/HTTP/browser/concurrency proof.'),indent=2,ensure_ascii=False)+'\n');print(len(ops),'source settings operations;',len(dto),'DTO cases')
if __name__=='__main__':asyncio.run(main())
