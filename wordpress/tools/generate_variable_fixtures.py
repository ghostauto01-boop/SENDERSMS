#!/usr/bin/env python3
"""Selected original registry AST with synthetic SQLite, plus pure original rendering code.
No application/config/environment imports or live provider/database calls.
"""
import ast,asyncio,datetime,importlib.util,itertools,json,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_contacts_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ns=cg.ns
ns['__name__']='offline_source_reference'
pure={};exec(compile((ROOT/'backend/app/utils/templating.py').read_text(),'source-templating','exec'),pure)
ns.update({k:v for k,v in pure.items() if not k.startswith('__')});ns.update(json=json,Mapping=typing.Mapping)
standard=next(n for n in ast.parse((ROOT/'backend/app/services/variable_service.py').read_text()).body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id','')=='STANDARD_VARIABLES')
ns['STANDARD_VARIABLES']=ast.literal_eval(standard.value);ns['_RESERVED']={'custom_fields','notes','id'}
(D/'sender-suite/schema/standard-variables.json').write_text(json.dumps(ns['STANDARD_VARIABLES'],ensure_ascii=False,indent=2)+'\n')
cg.load('backend/app/models/variable.py',{'ContactVariable'},models=True)
# Explicit SQLite adapter choice: monotonic committed IDs like PostgreSQL/MySQL, not SQLite ROWID reuse.
ns['ContactVariable'].__table__.dialect_options['sqlite']['autoincrement']=True
dto_ns=dict(ns,datetime=datetime.datetime)
cg.load('backend/app/schemas/variable.py',{'_valid_shortcode','VariableCreate','VariableUpdate','VariableOut'},dto_ns)
for n in ('VariableCreate','VariableUpdate','VariableOut'):ns[n]=dto_ns[n]
cg.load('backend/app/services/variable_service.py',{'humanize','_custom_values','ensure_standard_variables','sync_variables_from_contacts','variable_maps','render_for_contact','find_placeholders','analyze_message'})
cg.load('backend/app/api/v1/variables.py',{'list_variables','sync_variables','create_variable','update_variable','delete_variable','preview_message'})
phone={};node=next(n for n in ast.parse((ROOT/'backend/app/utils/phone.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='count_sms_segments');phone['Tuple']=typing.Tuple;exec(compile(ast.Module(body=[node],type_ignores=[]),'source-count-segments','exec'),phone)
contacts=[None,{},dict(first_name=' Ada ',last_name='Okafor',business_name='Acme'),dict(business_name='Shop'),dict(custom_fields=json.dumps({'Pain Point':'time','State':'FCT','count':0,'flag':False,'yes':True,'float':1.0,'nested':{'x':1},'0':'zero','amount':1e-7})),dict(first_name='0',business_name='0'),dict(custom_fields='{broken')]
bodies=['Hi {{ first_name }}, {{contact}} / {{name}}!','{{Pain Point}} {{State}} {{count}} {{flag}} {{yes}} {{float}} {{amount}} {{0}}','{{email| }} {{city| Abuja }} {{first_name|}}','{first_name} {email} {nonsense} {Pain Point}','{{ Brand-Name }}: {{unknown}} {{Unknown}} {{unknown|fallback}}','A , {{missing}},, B ({{missing}}) [ ] " " \' \'','\u3000; \u00a0\n {{first_name}}\n\n\n end','Hi {{fullname}} {{country}} {{business.name}}','{{phone_number}} {{website}} {{industry}}','{{missing}} {name} literal {braces}']
profiles=[dict(aliases={},fallbacks={},overrides={},known=[]),dict(aliases={'pain_point':'state','fullname':'full_name'},fallbacks={'state':'fallback state','first_name':'Guest'},overrides={'first_name':'Override','city':'0'},known=['missing']),dict(aliases={'0':'count','pain':'pain_point'},fallbacks={'city':'0'},overrides={},known=[])]
render=[]
for c,b,p,mode in itertools.product(contacts,bodies,profiles,['drop','mark']):
 contact=None if c is None else types.SimpleNamespace(**c)
 render.append(dict(contact=c,body=b,unknown=mode,**p,expected=pure['render_template'](b,contact,aliases=p['aliases'],fallbacks=p['fallbacks'],known=p['known'],unknown=mode,**p['overrides']),expected_context=pure['build_context'](contact),expected_unknown=pure['unknown_names'](b,p['known'])))
segments=[{'text':c*n,'expected':phone['count_sms_segments'](c*n)} for c in ['A','é','^','😀','\n'] for n in [0,1,67,70,71,153,160,161,306,307]]
ops=[]
def add(op,**kw):ops.append(dict(op=op,**kw))
add('list',sync=False);add('preview',body='Hi {{first_name}} {{unknown}}');add('sync');add('create',body={'field_key':'Pain Point','fallback_text':'More time'});add('create',body={'field_key':'pain_point'});add('create',body={'field_key':'other','shortcode':'pain_point'});add('create',body={'field_key':'niche','label':'0','shortcode':'topic','fallback_text':'0'});add('list',sync=False);add('preview',body='Hi {{first_name}} {{Pain Point}} {{topic}} {{unknown}}');add('update',id=12,body={'shortcode':'pain','label':'Pain renamed'});add('update',id=13,body={'shortcode':'pain'});add('delete',id=1);add('delete',id=404);add('delete',id=13);add('seed_contact',body={'first_name':'Ada','custom_fields':json.dumps({'Pain Point':'time','Empty':'','flag':False,'count':0,'notes':'private'})});add('sync');add('list',sync=False);add('preview',body='{{pain}} {{flag}} {{count}} {{Empty|x}} {{contact}}',contact_id=1);add('update',id=12,body={'is_active':False});add('preview',body='{{pain}} {{pain_point}} {{first_name}}',contact_id=1);add('list',search='PAIN',sync=False);add('list',source='standard',sync=False);add('update',id=404,body={'label':'no'});add('create',body={'field_key':'___'});add('delete',id=12);add('sync');add('list',sync=False)
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   try:
    kw=dict(db=db,current_user=None);kind=op['op'];out=None;status=200
    if kind=='list':out=await ns['list_variables'](search=op.get('search'),source=op.get('source'),sync=op.get('sync',True),**kw)
    elif kind=='sync':out=await ns['sync_variables'](**kw)
    elif kind=='create':out=await ns['create_variable'](data=ns['VariableCreate'].model_validate(op['body'],from_attributes=True),**kw);out=ns['VariableOut'].model_validate(out).model_dump(mode='json');status=201
    elif kind=='update':out=await ns['update_variable'](variable_id=op['id'],data=ns['VariableUpdate'].model_validate(op['body'],from_attributes=True),**kw);out=ns['VariableOut'].model_validate(out).model_dump(mode='json')
    elif kind=='delete':await ns['delete_variable'](variable_id=op['id'],**kw);status=204
    elif kind=='preview':out=await ns['preview_message'](body=op['body'],contact_id=op.get('contact_id'),**kw)
    elif kind=='seed_contact':out=await ns['create_contact'](data=ns['ContactCreate'].model_validate(dict(email='fixture@business.ng',**op['body'])),**kw);status=201
    await db.commit();op['expected']={'status':status,'body':cg.dump(out)}
   except cg.HTTPException as e:
    await db.rollback();op['expected']={'status':e.status_code,'message':e.detail}
   except cg.ValidationError as e:
    await db.rollback();err=e.errors()[0];op['expected']={'status':422,'message':err['msg'],'field':'.'.join(map(str,err['loc'])) or None}
 await engine.dispose()
 scalars=[{'input':v,'expected_clean':pure['_clean'](v),'expected_key':pure['normalize_key'](v)} for v in [None,False,True,0,1,-1,0.0,-0.0,1.0,1e-7,1e-5,1e-4,1e15,1e16,1e20,1.5e16,1.234e-5]]
 dto=[]
 for update in (False,True):
  for body in [None,[],{}, {'field_key':'0'},{'field_key':'Pain Point'},{'field_key':None},{'field_key':4},{'field_key':'_'},{'field_key':'x'*151},{'field_key':'ok','label':'x'*201},{'field_key':'ok','shortcode':' '},{'field_key':'ok','fallback_text':'x'*501},{'field_key':'ok','shortcode':None},{'shortcode':None},{'label':None},{'is_active':'yes'}]:
   try:expected={'body':ns['VariableUpdate' if update else 'VariableCreate'].model_validate(body,from_attributes=True).model_dump(exclude_unset=update)}
   except cg.ValidationError as e:
    err=e.errors()[0];expected={'message':err['msg'],'field':'.'.join(map(str,err['loc'])) or None}
   dto.append(dict(update=update,input=body,expected=expected))
 (D/'tests/variable-source-fixtures.json').write_text(json.dumps(dict(render=render,segments=segments,scalars=scalars,operations=ops,dto=dto,scope='Pure original renderer plus selected registry AST, fixed clock and synthetic SQLite with explicit monotonic registry IDs; no native HTTP, WP, MySQL, provider or carrier-billing certification'),ensure_ascii=False,indent=2)+'\n');print(len(render),'render cases;',len(ops),'registry operations;',len(dto),'DTO cases;',len(segments),'segment cases')
if __name__=='__main__':asyncio.run(main())
