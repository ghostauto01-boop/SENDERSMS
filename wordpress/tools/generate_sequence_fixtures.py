#!/usr/bin/env python3
"""Actual selected sequence schemas/service/routes, isolated SQLite, fixed UTC clock; no app/provider imports."""
import ast,asyncio,importlib.util,json,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_analytics_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
from pydantic import model_validator
from sqlalchemy.orm import relationship,foreign
ns.update(json=json,model_validator=model_validator,Any=typing.Any,Iterable=typing.Iterable)
cg.load('backend/app/models/template.py',{'Template'},models=True);cg.load('backend/app/models/sequence.py',{'Sequence','SequenceStep','SequenceVersion'},models=True)
# Reconstruct only the source's two delete-orphan relationships; FKs elsewhere remain omitted.
ns['Sequence'].steps=relationship(ns['SequenceStep'],primaryjoin=ns['Sequence'].id==foreign(ns['SequenceStep'].sequence_id),cascade='all, delete-orphan',order_by=ns['SequenceStep'].step_order)
ns['Sequence'].versions=relationship(ns['SequenceVersion'],primaryjoin=ns['Sequence'].id==foreign(ns['SequenceVersion'].sequence_id),cascade='all, delete-orphan')
for name in ('Sequence','SequenceStep','SequenceVersion'):ns[name].__table__.dialect_options['sqlite']['autoincrement']=True
for path,names in [('backend/app/schemas/sequence.py',{'StepType','SequenceChannel','ConditionType'}),('backend/app/services/sequence_service.py',{'ALLOWED_STEP_TYPES','SEND_STEP_TYPES','ALLOWED_CHANNELS','ALLOWED_CONDITIONS','MAX_SEQUENCE_STEPS','MAX_STEP_MESSAGE_LENGTH','MAX_STEP_EMAIL_LENGTH'})]:
 tree=ast.parse((ROOT/path).read_text());nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in names for t in n.targets)];exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-sequence-constants','exec'),ns)
cg.load('backend/app/schemas/sequence.py',{'SequenceStepCreate','SequenceCreate','SequenceUpdate'})
cg.load('backend/app/services/sequence_service.py',{'step_message_body','message_config','snapshot_steps','step_is_send','validate_sequence_steps'})
cg.load('backend/app/api/v1/sequences.py',{'_current_steps','_step_item','_sequence_item','list_sequences','get_sequence','create_sequence','update_sequence','delete_sequence'})
helpers=[dict(input=s,expected=ns['step_message_body'](s)) for s in [None,'','0','false','null','hello','"hello"','"  "','[]','{"message_body":"Hi é"}','{"message_body":0}','{"message_body":true}','{"message_body":["x",1]}','{"message_body":{"x":false}}','{"other":true}','{bad']]
def send(channel='sms',**kw):return dict(step_order=0,step_type='send_email' if channel=='email' else 'send_sms',config='{"message_body":"Hello"}',**({'subject':'Subject'} if channel=='email' else {}),**kw)
bodies=[None,[],{},dict(name='Missing defaults'),dict(name='X',steps=[]),dict(name='X',steps=[send()]),dict(name=' ',steps=[send()]),dict(name='X',steps=[dict(step_type='wait',step_order=0)]),dict(name='X',steps=[dict(step_type='condition',step_order=0)]),dict(name='X',steps=[dict(step_type='send_email',step_order=0)]),dict(name='X',channel='EMAIL',steps=[send()]),dict(name='X',steps=[dict(send(),step_order=50)]),dict(name='X',steps=[dict(send(),template_id=0)]),dict(name='X',steps=[dict(send(),wait_duration_hours=8761)]),dict(name='X',steps=[dict(send(),config='x'*10001)]),dict(name='X',steps=[dict(send(),step_order='0.0')]),dict(name='X',steps=[dict(send(),condition_type='bad')]),dict(name='X',steps=[dict(send(),subject='x'*501)]),dict(name='X',steps=[dict(send(),email_account_id=-3)]),dict(name=None),dict(name='X',steps=None)]
dtos=[]
for update in (False,True):
 for body in bodies:
  try:
   d=ns['SequenceUpdate' if update else 'SequenceCreate'].model_validate(body);out=d.model_dump(exclude_unset=update)
   # Native service adapter needs full per-step model attributes, not recursive exclude_unset serialization.
   if d.steps is not None and ('steps' in out):out['steps']=[step.model_dump() for step in d.steps]
   dtos.append(dict(update=update,input=body,ok=True,expected=out))
  except cg.ValidationError:dtos.append(dict(update=update,input=body,ok=False))
ops=[]
def add(kind,**kw):ops.append(dict(kind=kind,**kw))
add('create',body=dict(name=' First ',steps=[dict(step_order=1,step_type='wait',wait_duration_hours=3),send()]))
add('get',id=1);add('list');add('update',id=1,body={'description':'Changed','steps':[send(),dict(step_order=1,step_type='stop')]});add('get',id=1)
for steps in [[dict(step_order=1,step_type='send_sms',config='hello')],[send(),send()],[dict(step_order=0,step_type='wait',wait_duration_hours=1)],[dict(send(),config=None)],[send(),dict(step_order=1,step_type='condition',condition_type='contact_replied',true_branch_step_order=0)],[send(),dict(step_order=1,step_type='condition',condition_type='contact_replied',false_branch_step_order=3)],[dict(send(),config='é'*5001)],[dict(send(),template_id=404)],[dict(send(),template_id=3)],[dict(send(),template_id=4)],[dict(send(),template_id=2)],[send('email')]]:add('update',id=1,body=dict(name='Should rollback',steps=steps))
add('update',id=1,body=dict(channel='email',steps=[send('email')]))
add('update',id=1,body=dict(steps=[dict(send('email'),template_id=5)]))
add('get',id=1);add('list',channel='email');add('list',channel='fax');add('list',search='irs%')
add('seed_campaign',sequence_id=1,status='running');add('update',id=1,body=dict(channel='sms',steps=[send()]));add('delete',id=1)
add('create',body=dict(name='Delete me',steps=[send()]));add('update',id=2,body=dict(steps=[dict(send(),config='Updated')]));add('delete',id=2);add('get',id=2)
add('create',body=dict(name='Follow-up guard',steps=[send()]));add('seed_followup',sequence_id=3);add('delete',id=3)
add('update',id=1,body={'description':None,'steps':None,'name':None,'channel':None});add('update',id=1,body={'is_active':False});add('get',id=1);add('get',id=404);add('delete',id=404);add('list',page=2,per_page=1);add('create',body={'name':'No steps'})
async def snapshot(db):
 out={}
 for table,model,fields in [('sequences','Sequence',['id','name','description','channel','current_version','is_active']),('steps','SequenceStep',['id','sequence_id','version','step_order','step_type','config','wait_duration_hours','template_id','subject','email_account_id','condition_type','condition_value','true_branch_step_order','false_branch_step_order']),('versions','SequenceVersion',['id','sequence_id','version','snapshot'])]:
  rows=(await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all();items=[]
  for r in rows:
   item={key:getattr(r,key) for key in fields}
   if 'snapshot' in item:item['snapshot']=json.loads(item['snapshot'])
   items.append(item)
  out[table]=items
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  db.add(ns['Contact'](email='sequence@business.ng'))
  for n,channel,body,active,subject in [('SMS','sms','body',True,None),('Email','email','body',True,'Subject'),('Inactive','sms','body',False,None),('Empty','sms',' ',True,None),('Legacy any','any','body',True,None)]:db.add(ns['Template'](name=n,channel=channel,body=body,is_active=active,subject=subject))
  await db.commit()
 for op in ops:
  async with sessions() as db:
   try:
    kind=op['kind'];kwargs=dict(db=db,current_user=None)
    if kind in ('create','update'):kwargs['data']=ns['SequenceUpdate' if kind=='update' else 'SequenceCreate'].model_validate(op['body'])
    if kind in ('get','update','delete'):kwargs['sequence_id']=op['id']
    if kind=='list':kwargs.update(page=op.get('page',1),per_page=op.get('per_page',25),search=op.get('search'),channel=op.get('channel'))
    if kind=='seed_campaign':db.add(ns['Campaign'](name='Live',sequence_id=op['sequence_id'],status=op['status']));out=None
    elif kind=='seed_followup':db.add(ns['FollowUp'](contact_id=1,sequence_id=op['sequence_id'],scheduled_at=cg.NOW));out=None
    else:out=await ns[{'create':'create_sequence','get':'get_sequence','update':'update_sequence','delete':'delete_sequence','list':'list_sequences'}[kind]](**kwargs)
    await db.commit();op['expected']=dict(status=201 if kind=='create' else 204 if kind=='delete' else 200,body=cg.dump(out))
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   except cg.ValidationError:await db.rollback();op['expected']=dict(status=422)
   op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/sequence-source-fixtures.json').write_text(json.dumps(dict(helpers=helpers,dto=dtos,operations=ops,scope='Actual selected schemas/service/routes; fixed clock, synthetic SQLite, monotonic committed IDs, omitted FKs and reconstructed two delete-orphan relationships only. UTC timestamp normalization required after SQLite refresh. No native WP/MySQL/concurrency/campaign execution or sends.'),indent=2,ensure_ascii=False)+'\n');print(len(helpers),'helpers;',len(dtos),'DTO cases;',len(ops),'source operations with snapshots')
if __name__=='__main__':asyncio.run(main())
