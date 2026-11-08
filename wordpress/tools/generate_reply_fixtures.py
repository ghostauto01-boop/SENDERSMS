#!/usr/bin/env python3
"""Original rule DTO/CRUD/dry-run AST with isolated SQLite and fixed time.
Classifier is a pure stdlib module. No original app imports, env, providers or sends. DTO root checks use from_attributes=True to model the FastAPI v2 adapter; no actual HTTP certification.
"""
import ast,asyncio,datetime,importlib.util,itertools,json,random,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_variables_reference',D/'tools/generate_variable_fixtures.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);cg=v.cg;ns=v.ns
classifier={};exec(compile((ROOT/'backend/app/services/ai_classifier.py').read_text(),'source-ai-classifier','exec'),classifier)
lexicon=dict(positive=classifier['POSITIVE_WORDS'],negative=classifier['NEGATIVE_WORDS'],intents=[list(t[:2]) for t in classifier['INTENT_PATTERNS']],positive_confirm=classifier['_POSITIVE_CONFIRM'].pattern,negative_confirm=classifier['_NEGATIVE_CONFIRM'].pattern,leading_negation=classifier['_LEADING_NEGATION'].pattern)
(D/'sender-suite/schema/reply-classifier.json').write_text(json.dumps(lexicon,ensure_ascii=False,indent=2)+'\n')
browser_provider=next(ast.literal_eval(n.value) for n in ast.parse((ROOT/'backend/app/services/push_service.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BROWSER_PROVIDER' for t in n.targets))
ns.update(desc=cg.sa.desc,update=cg.sa.update,BROWSER_PROVIDER=browser_provider,Any=typing.Any,date=datetime.date,Date=cg.sa.Date,Index=cg.sa.Index,classify=classifier['classify'])
for path,names in [('backend/app/models/notification.py',{'NotificationEvent'}),('backend/app/models/autoreply.py',{'AutoReplyRule'}),('backend/app/models/automation.py',{'Automation'}),('backend/app/models/email.py',{'_now','EmailAccount'}),('backend/app/models/email_inbox.py',{'EmailMailbox'})]:cg.load(path,names,models=True)
for model in ('AutoReplyRule','Automation','EmailAccount','EmailMailbox','NotificationEvent'):ns[model].__table__.dialect_options['sqlite']['autoincrement']=True
for node in ast.parse((ROOT/'backend/app/schemas/automation.py').read_text()).body:
 if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('CONDITION_FIELDS','ACTION_TYPES'):ns[node.targets[0].id]=ast.literal_eval(node.value)
dto_ns=dict(ns,datetime=datetime.datetime)
models=('AutoReplyRuleCreate','AutoReplyRuleUpdate','AutoReplyRuleOut','AutoReplyTestRequest','AutoReplyTestResponse','AutomationCreate','AutomationUpdate','AutomationOut');dto_ns['MatchType']=typing.Literal['contains','exact','starts','any']
cg.load('backend/app/schemas/autoreply.py',set(models),dto_ns);cg.load('backend/app/schemas/automation.py',set(models),dto_ns)
for n in models:ns[n]=dto_ns[n]
cg.load('backend/app/services/email_service.py',{'get_account','get_sender'});cg.load('backend/app/services/mailbox_service.py',{'get_mailbox'})
ns['email_service']=types.SimpleNamespace(get_sender=ns['get_sender']);ns['timedelta']=datetime.timedelta
cg.load('backend/app/services/autoreply_service.py',{'rule_matches','AutoReplyService'});cg.load('backend/app/services/automation_service.py',{'load_json','AutomationService'})
cg.load('backend/app/api/v1/autoreply.py',{'_validate','list_rules','create_rule','get_rule','update_rule','delete_rule','test_rules'})
cg.load('backend/app/api/v1/automations.py',{'_dump','list_automations','create_automation','get_automation','update_automation','delete_automation','test_automation'})
cg.load('backend/app/api/v1/notifications.py',{'_out','list_notifications','unread_count','mark_read','mark_all_read'})
rng=random.Random(701)
texts=['','0',' YES O ','no wahala','abeg remove me','wrong number, remove me','not interested','yes please send pricing','how much','what time tomorrow','hello','na we','no, not us','interested o','STOP','thanks','İnterested','K','ſtop','éyesé','😀yes😀']
texts+=list(classifier['POSITIVE_WORDS'])+list(classifier['NEGATIVE_WORDS'])
texts += [' '.join(rng.choices(list(classifier['POSITIVE_WORDS'])+list(classifier['NEGATIVE_WORDS']),k=5)) for _ in range(120)]
classification=[dict(text=t,expected=classifier['classify'](t)) for t in texts]
matching=[]
for match_type,keywords,body in itertools.product(['any','contains','exact','starts','unknown'],[None,'','price,pricing, yes','  ,  ,',' İSTANBUL,😀,0'],['',' YES ','price?','  pricing ','xpricing','0','😀','i̇stanbul']):
 rule=ns['AutoReplyRule'](match_type=match_type,keywords=keywords);matching.append(dict(rule={'match_type':match_type,'keywords':keywords},body=body,expected=ns['rule_matches'](rule,body)))
contexts=[dict(text='YES Price',sentiment='positive',intent='interested',labels=['yes','Positive'],tags={'buyer'}),dict(text='',sentiment=None,intent=None,labels=[],tags=set())]
conditions=[];svc=ns['AutomationService'](None)
for ctx,field,op,value in itertools.product(contexts,['any','sentiment','intent','label','keyword','has_tag','unknown'],['is','is_not','not','contains','in','unknown'],['positive','YES','buyer','',0,True]):
 cond=dict(field=field,op=op,value=value);conditions.append(dict(condition=cond,context=dict(ctx,tags=sorted(ctx['tags'])),expected=svc._condition_met(cond,ctx)))
ops=[]
def add(op,automation=False,**kw):ops.append(dict(op=op,automation=automation,**kw))
add('list');add('test',body={'body':'price?'})
add('create',body={'name':'Invalid','reply_body':'Reply'})
add('create',body={'name':'Price','keywords':'price,pricing','reply_body':'Hi {{first_name}} — our prices','priority':20,'stop_on_match':False})
add('create',body={'name':'Catch-all','match_type':'any','reply_body':'Thanks','priority':100})
add('create',body={'name':'Email','channel':'email','match_type':'any','reply_body':'Mail'})
add('matching',body='price');add('matching',body='',channel='sms');add('matching',body='price',channel='email');add('matching',body='price',channel='fax');
add('create',body={'name':'Bad channel','channel':'fax','match_type':'any','reply_body':'X'})
add('create',body={'name':'No sender','channel':'email','email_account_id':404,'match_type':'any','reply_body':'X'})
add('create',body={'name':'No mailbox','channel':'email','email_account_id':-404,'match_type':'any','reply_body':'X'})
add('create',body={'name':'Sender zero','channel':'email','email_account_id':0,'match_type':'any','reply_body':'X'})
add('list',channel='all');add('list',channel='EMAIL');add('list',channel='fax');add('get',id=1);add('get',id=404);add('test',body={'body':'PRICE?'})
add('update',id=1,body={'keywords':''});add('update',id=1,body={'reply_body':' '});add('update',id=1,body={'is_enabled':False});add('test',body={'body':'price'});add('update',id=1,body={'is_enabled':True,'priority':0,'stop_on_match':True});add('test',body={'body':'pricing'});add('update',id=1,body={'match_type':'any','keywords':None});add('test',body={'body':''});add('delete',id=2);add('get',id=2)
add('list',True);add('create',True,body={'name':'  Positive  ','conditions':[{'field':'sentiment','value':'positive'}],'actions':[{'type':'add_tag','value':'warm'}]});add('create',True,body={'name':'Email','channel':'email','conditions':[{'field':'any'}],'actions':[]});add('create',True,body={'name':'Coerced channel','channel':'fax','conditions':[{'field':'keyword','value':'price'}],'priority':0});add('create',True,body={'name':'Missing sender','channel':'email','email_account_id':404});add('create',True,body={'name':'Invalid condition','conditions':[{'field':'bad'}]});add('create',True,body={'name':'Invalid action','actions':[{'type':'send_sms','body':' '}]});add('list',True,channel='all');add('test',True,body={'text':'yes please'});add('test',True,body={'text':'what price?'});add('test',True,body={'text':' '});add('update',True,id=1,body={'name':' '});add('update',True,id=1,body={'conditions':[{'field':'has_tag','value':'buyer'}]});add('test',True,body={'text':'Hello','tags':['BUYER']});add('update',True,id=1,body={'is_enabled':False});add('test',True,body={'text':'Hello','tags':['buyer']});add('update',True,id=1,body={'channel':'custom','conditions':None,'actions':None});add('get',True,id=1);add('delete',True,id=2);add('get',True,id=2)
# Synthetic sender rows, no credentials or connections. Exercises positive and unified negative ids.
add('seed_sender',body={});add('create',body={'name':'Saved sender','channel':'email','email_account_id':1,'match_type':'any','reply_body':'Mail'});add('create',True,body={'name':'Saved mailbox','channel':'email','email_account_id':-1,'conditions':[{'field':'any'}]});add('list',True,channel='all')
add('notification_list');add('notification_unread');add('notification_seed',body=[{'event_type':'test','provider':'browser','title':'One','body':'Fixture only'},{'event_type':'test','provider':'pushover','title':'Private provider','is_read':False},{'event_type':'test','provider':'browser','title':'Read','is_read':True}]);add('notification_list');add('notification_list',unread_only=True);add('notification_unread');add('notification_read',id=2);add('notification_read',id=404);add('notification_read',id=1);add('notification_read',id=1);add('notification_unread');add('notification_read_all');add('notification_list',page=2,per_page=1);add('notification_list',unread_only=True)
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   try:
    kw=dict(db=db,current_user=None);kind=op['op'];a=op['automation'];status=200
    if kind=='notification_seed':
     for row in op['body']:db.add(ns['NotificationEvent'](**row))
     await db.commit();out=None
    elif kind=='notification_list':out=await ns['list_notifications'](page=op.get('page',1),per_page=op.get('per_page',20),unread_only=op.get('unread_only',False),db=db,cu=None)
    elif kind=='notification_unread':out=await ns['unread_count'](db=db,cu=None)
    elif kind=='notification_read':out=await ns['mark_read'](event_id=op['id'],db=db,cu=None)
    elif kind=='notification_read_all':out=await ns['mark_all_read'](db=db,cu=None)
    elif kind=='create':out=await ns['create_automation' if a else 'create_rule'](payload=ns['AutomationCreate' if a else 'AutoReplyRuleCreate'].model_validate(op['body']),**kw);status=201
    elif kind=='update':out=await ns['update_automation' if a else 'update_rule'](**{'automation_id' if a else 'rule_id':op['id']},payload=ns['AutomationUpdate' if a else 'AutoReplyRuleUpdate'].model_validate(op['body']),**kw)
    elif kind=='list':out=await ns['list_automations' if a else 'list_rules'](channel=op.get('channel'),**kw)
    elif kind=='get':out=await ns['get_automation' if a else 'get_rule'](**{'automation_id' if a else 'rule_id':op['id']},**kw)
    elif kind=='delete':out=await ns['delete_automation' if a else 'delete_rule'](**{'automation_id' if a else 'rule_id':op['id']},**kw);status=204
    elif kind=='matching':out=[r.id for r in await ns['AutoReplyService'](db).find_matching_rules(op['body'],channel=op.get('channel','sms'))]
    elif kind=='test':out=await ns['test_automation'](body=op['body'],**kw) if a else await ns['test_rules'](payload=ns['AutoReplyTestRequest'].model_validate(op['body']),**kw)
    elif kind=='seed_sender':
     db.add(ns['EmailAccount'](name='Fixture sender',from_name='Fixture',from_email='fixture@example.invalid'));db.add(ns['EmailMailbox'](name='Fixture mailbox',email_address='fixture@example.invalid'));await db.commit();out=None
    if isinstance(out,ns['AutoReplyRule']):out=ns['AutoReplyRuleOut'].model_validate(out).model_dump()
    await db.commit();op['expected']=dict(status=status,body=cg.dump(out))
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   except cg.ValidationError as e:
    await db.rollback();err=e.errors()[0];op['expected']=dict(status=422,message=err['msg'],field='.'.join(map(str,err['loc'])) or None)
   op['notification_unread']=(await db.execute(cg.sa.select(cg.sa.func.count()).where(ns['NotificationEvent'].provider=='browser',ns['NotificationEvent'].is_read.is_(False)))).scalar()
   op['count']=(await db.execute(cg.sa.select(cg.sa.func.count()).select_from(ns['Automation' if a else 'AutoReplyRule']))).scalar()
 await engine.dispose()
 dto=[]
 bodies=[None,[],{}, {'name':None},{'name':4},{'name':''},{'name':' '},{'name':'x'*161},{'name':'ok','reply_body':''},{'name':'ok','reply_body':' '},{'name':'ok','reply_body':None},{'name':'ok','match_type':'bad'},{'name':'ok','match_type':None},{'name':'ok','subject':'é'*501},{'name':'ok','is_enabled':None},{'name':'ok','is_enabled':'false'},{'name':'ok','priority':'1.0'},{'name':'ok','priority':True},{'name':'ok','cooldown_minutes':-1},{'name':'ok','cooldown_minutes':100001},{'name':'ok','conditions':[{'field':'bad'}]},{'name':'ok','conditions':[4]},{'name':'ok','conditions':None},{'name':'ok','actions':[{'type':'bad'}]},{'name':'ok','actions':[{'type':'send_email','body':' '}]},{'name':'ok','channel':None},{'name':'ok','extra':5}]
 for a,update,body in itertools.product([False,True],[False,True],bodies):
  try:expected={'body':ns[('Automation' if a else 'AutoReplyRule')+('Update' if update else 'Create')].model_validate(body,from_attributes=True).model_dump(exclude_unset=update)}
  except cg.ValidationError as e:
   err=e.errors()[0];expected=dict(message=err['msg'],field='.'.join(map(str,err['loc'])) or None)
  dto.append(dict(automation=a,update=update,input=body,expected=expected))
 (D/'tests/reply-source-fixtures.json').write_text(json.dumps(dict(operations=ops,dto=dto,classification=classification,matching=matching,conditions=conditions,scope='Original selected rule/notification AST, pure original classifier, synthetic SQLite, fixed clock, omitted relationships; no inbound action execution/cooldown/send/provider/native DB/HTTP/concurrency certification'),ensure_ascii=False,indent=2)+'\n');print(len(ops),'operations;',len(dto),'DTO;',len(classification),'classifier;',len(matching),'matching;',len(conditions),'condition cases')
if __name__=='__main__':asyncio.run(main())
