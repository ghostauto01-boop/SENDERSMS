#!/usr/bin/env python3
"""Original selected template/attachment/preview AST, not original application imports.
Synthetic SQLite, fixed clock, omitted relationships and explicit monotonic committed IDs.
No provider/network/environment/production database access.
"""
import ast,asyncio,base64,datetime,hashlib,importlib.util,json,logging,types
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_variables_reference',D/'tools/generate_variable_fixtures.py');vg=importlib.util.module_from_spec(spec);spec.loader.exec_module(vg);cg=vg.cg;ns=vg.ns
cg.load('backend/app/models/template.py',{'Template'},models=True);ns['Template'].__table__.dialect_options['sqlite']['autoincrement']=True
cg.load('backend/app/schemas/template.py',{'TemplateCreate','TemplateUpdate','TemplateOut'},dict_ns:=dict(ns,datetime=datetime.datetime))
for n in ('TemplateCreate','TemplateUpdate','TemplateOut'):ns[n]=dict_ns[n]
ns.update(MAX_ATTACHMENTS=10,MAX_ATTACHMENT_BYTES=4*1024*1024,MAX_ATTACHMENTS_TOTAL_BYTES=8*1024*1024,VALID_CHANNELS=('sms','email'),DEFAULT_SAMPLE={'first_name':'John','last_name':'Doe','business_name':'Acme Ltd','phone_number':'08012345678','email':'john.doe@example.com','city':'Lagos','state':'Lagos','website':'https://example.com','industry':'Technology'})
ns['count_sms_segments']=vg.phone['count_sms_segments']
cg.load('backend/app/services/email_service.py',{'clean_attachments','dump_attachments','load_attachments','attachment_summary'})
cg.load('backend/app/services/variable_service.py',{'known_variable_keys','unknown_in','suggest','unknown_variables_error','render_preview'})
ns['variable_service']=types.SimpleNamespace(**{n:ns[n] for n in ['unknown_in','unknown_variables_error','render_preview']})
cg.load('backend/app/api/v1/templates.py',{'_clean_channel','_refuse_unknown_variables','_require_subject','_store_attachments','_template_out','_email_metrics','list_templates','list_categories','preview_template','get_template','create_template','update_template','delete_template','duplicate_template','convert_to_email'})
logging.getLogger('offline-reference').addHandler(logging.NullHandler());logging.getLogger('offline-reference').propagate=False
attachments=[]
for items in [None,[],[4,None,{},[],{'name':'x','content':'YQ=='}],[{'name':'0','content_base64':'YQ=='},{'name':0,'content':'YQ=='},{'name':True,'content':True},{'name':{'x':'y'},'content':'YQ=='}],[{'name':'😀'*256,'content':'data:text/plain;base64,YQ==','content_type':'é'*121}],[{'name':'x','content':s} for s in ['YQ==','YQ','YQ=','YQ=abc','YQ==abc','Y Q = = \n','abc','abcd','a===bcd','====','a','!','@@@@','é','data:'+75*'a'+',YQ==']]]:
 cleaned=ns['clean_attachments'](items);raw=ns['dump_attachments'](cleaned);attachments.append({'input':items,'expected':cleaned,'expected_dump':raw,'expected_summary':ns['attachment_summary'](raw)})
boundaries=[]
for sizes in [[0],[4*1024*1024],[4*1024*1024+1,2],[4*1024*1024,4*1024*1024,1],[1]*11,[3*1024*1024]*3]:
 items=[{'name':f'file-{i}','content':base64.b64encode(b'a'*n).decode()} for i,n in enumerate(sizes)];cleaned=ns['clean_attachments'](items);raw=ns['dump_attachments'](cleaned);boundaries.append({'sizes':sizes,'summary':ns['attachment_summary'](raw),'stored_sha256':hashlib.sha256(raw.encode()).hexdigest() if raw else None})
suggestions=[{'name':n,'known':known,'expected':ns['suggest'](n,set(known))} for known in [[],['first_name','last_name','full_name','name'],['pain','paid','pain_point','point','city'],['a'*150,'a'*149+'b','b'*150]] for n in ['frist_name','firs name','painpont','pian','unknown','name','x'*201,'a'*201,'a'*149+'c']]
ops=[]
def add(op,**kw):ops.append(dict(op=op,**kw))
add('create',body={'name':'Welcome','category':'SMS','body':'Hi {{first_name}}','subject':'ignored','attachments':[{'name':'ignored','content':'YQ=='}],'include_unsubscribe':False})
add('create',body={'name':'Bad','body':'{{frist_name}} {{unobtainium}}'})
add('create',body={'name':'Email no body','channel':'email','subject':'Subject'})
add('create',body={'name':'Email no subject','channel':'email','body':'Text'})
add('create',body={'name':'Email','category':'Mail','channel':'email','subject':'Hi {{business_name}}','body':'Plain','html_body':'<b>{{first_name}}</b>','attachments':[{'name':'test.txt','content_base64':'YQ=='}],'include_unsubscribe':False})
add('create',body={'name':'Zeros','channel':'email','subject':'0','body':'0'})
add('list');add('list',channel='sms');add('list',channel='email');add('list',channel='bad');add('list',search='welcome');add('list',category='SMS');add('list',page=2,per_page=1);add('categories');add('categories',channel='email');add('get',id=1);add('get',id=404)
add('update',id=1,body={'body':'^'*71,'is_active':False});add('duplicate',id=1);add('to_email',id=1);add('to_email',id=2);add('update',id=2,body={'subject':' '});add('update',id=2,body={'body':'New {{city}}','attachments':None});add('update',id=1,body={'body':'{{frist_name}}'});add('get',id=1)
add('preview',body='Hi {{first_name}}, {{brand}} {{frist_name}}',subject='{{business_name}}',channel='email');add('preview',body='Hello {{first_name}}',first_name='Ada');add('preview',body='x',contact_id=0)
add('seed_contact',body={'first_name':'Ada','business_name':'Shop','custom_fields':json.dumps({'Pain Point':'time'})});add('create',body={'name':'Custom','body':'Hi {{pain_point}}'});add('preview',body='Hi {{first_name}} {{Pain Point}} {{Pain Point|fallback}}',contact_id=1);add('create',body={'name':'Unknown email','channel':'email','subject':'{{frist_name}}','html_body':'<p>Mail</p>'});add('delete',id=3);add('get',id=3);add('list')
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   try:
    kw=dict(db=db,current_user=None);kind=op['op'];status=200;out=None
    if kind=='create':out=await ns['create_template'](data=ns['TemplateCreate'].model_validate(op['body'],from_attributes=True),**kw);status=201
    elif kind=='update':out=await ns['update_template'](template_id=op['id'],data=ns['TemplateUpdate'].model_validate(op['body'],from_attributes=True),**kw)
    elif kind=='list':out=await ns['list_templates'](**{k:v for k,v in op.items() if k!='op'},**kw)
    elif kind=='categories':out=await ns['list_categories'](channel=op.get('channel'),**kw)
    elif kind=='get':out=await ns['get_template'](template_id=op['id'],**kw)
    elif kind=='duplicate':out=await ns['duplicate_template'](template_id=op['id'],**kw)
    elif kind=='to_email':out=await ns['convert_to_email'](template_id=op['id'],**kw)
    elif kind=='delete':await ns['delete_template'](template_id=op['id'],**kw);status=204
    elif kind=='preview':out=await ns['preview_template'](**{k:v for k,v in op.items() if k!='op'},**kw)
    elif kind=='seed_contact':out=await ns['create_contact'](data=ns['ContactCreate'].model_validate(dict(email='fixture@business.ng',**op['body'])),**kw);status=201
    await db.commit();op['expected']={'status':status,'body':cg.dump(out)}
   except cg.HTTPException as e:
    await db.rollback();op['expected']={'status':e.status_code,('detail' if isinstance(e.detail,dict) else 'message'):e.detail}
   except cg.ValidationError as e:
    await db.rollback();err=e.errors()[0];op['expected']={'status':422,'message':err['msg'],'field':'.'.join(map(str,err['loc'])) or None}
   op['template_count']=(await db.execute(cg.sa.select(cg.sa.func.count()).select_from(ns['Template']))).scalar()
 await engine.dispose()
 dto=[]
 for update in (False,True):
  for body in [None,[],{}, {'name':None},{'name':5},{'name':'x'*256},{'name':'ok','body':None},{'name':'ok','channel':'fax'},{'name':'ok','channel':None},{'name':'ok','subject':'é'*501},{'name':'ok','preheader':'x'*501},{'name':'ok','attachments':{}},{'name':'ok','attachments':[4]},{'name':'ok','attachments':[[]]},{'name':'ok','attachments':[{}]},{'name':'ok','email_account_id':'1.0'},{'name':'ok','email_account_id':False},{'name':'ok','include_unsubscribe':'false'},{'name':'ok','include_unsubscribe':None},{'is_active':None},{'name':'ok','extra':'ignored'}]:
   try:expected={'body':ns['TemplateUpdate' if update else 'TemplateCreate'].model_validate(body,from_attributes=True).model_dump(exclude_unset=update)}
   except cg.ValidationError as e:
    err=e.errors()[0];expected={'message':err['msg'],'field':'.'.join(map(str,err['loc'])) or None}
   dto.append(dict(update=update,input=body,expected=expected))
 (D/'tests/template-source-fixtures.json').write_text(json.dumps(dict(operations=ops,dto=dto,attachments=attachments,boundaries=boundaries,suggestions=suggestions,scope='Selected original template/attachment/preview AST; synthetic SQLite with explicit monotonic committed IDs, fixed clock and omitted relationships; not native DB/HTTP/WP/provider/HTML-safety certification'),ensure_ascii=False,indent=2)+'\n');print(len(ops),'template operations;',len(dto),'DTO cases;',len(attachments),'attachment cases;',len(boundaries),'size boundaries;',len(suggestions),'suggestion cases')
if __name__=='__main__':asyncio.run(main())
