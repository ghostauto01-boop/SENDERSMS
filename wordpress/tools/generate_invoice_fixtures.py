#!/usr/bin/env python3
"""Selected original invoice source AST; synthetic SQLite/UTC/clock/UUID/settings. No provider imports or settlement."""
import ast,asyncio,datetime,importlib.util,json,math,random,re,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_invoice_reference',D/'tools/generate_analytics_fixtures.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref);cg=ref.cg;ns=ref.ns
from sqlalchemy.orm import relationship,foreign,noload
ns.update(Float=cg.sa.Float,json=json,math=math,re=re,Any=typing.Any,Iterable=typing.Iterable,sa_inspect=cg.sa.inspect,noload=noload,public_base_url=lambda:'',settings=types.SimpleNamespace(APP_NAME='Sendify',PAYSTACK_SECRET_KEY='',PAYSTACK_PUBLIC_KEY=''))
serial=0
def uuid4():
 global serial
 serial+=1;return types.SimpleNamespace(hex=f'{serial:032x}')
ns['uuid4']=uuid4
cg.load('backend/app/models/invoice.py',{'_now','_token'})
cg.load('backend/app/models/invoice.py',{'Invoice','InvoiceItem','InvoicePayment','InvoiceEvent'},models=True)
cg.load('backend/app/models/system.py',{'SystemSetting'},models=True)
for name in ('Invoice','InvoiceItem','InvoicePayment','InvoiceEvent'):ns[name].__table__.dialect_options['sqlite']['autoincrement']=True
for attr,child,order in [('items','InvoiceItem',ns['InvoiceItem'].position),('payments','InvoicePayment',ns['InvoicePayment'].id.desc()),('events','InvoiceEvent',ns['InvoiceEvent'].id.desc())]:setattr(ns['Invoice'],attr,relationship(ns[child],primaryjoin=ns['Invoice'].id==foreign(ns[child].invoice_id),cascade='all, delete-orphan',lazy='selectin',order_by=order))
class SQLiteUTC(cg.sa.TypeDecorator):
 impl=cg.sa.DateTime;cache_ok=True
 def process_result_value(self,value,dialect):return value.replace(tzinfo=datetime.timezone.utc) if value is not None else None
for name in ('Invoice','InvoiceEvent','InvoicePayment'):
 for col in ns[name].__table__.columns:
  if isinstance(col.type,cg.sa.DateTime):col.type=SQLiteUTC()
for path in ['backend/app/models/invoice.py','backend/app/services/invoice_service.py']:
 nodes=[n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id','').startswith(('S_','DEFAULT_','CURRENCY_','INVOICE_')) for t in n.targets)];exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-invoice-constants','exec'),ns)
cg.load('backend/app/services/system_settings.py',{'get_setting'})
ns['decrypt_value']=lambda value:'synthetic-unread-secret'
cg.load('backend/app/services/invoice_service.py',{'currency_symbol','_clean_currency','_clean_prefix','get_invoice_settings','_minor','calculate_totals','_as_utc','_iso','next_invoice_number','effective_status','status_label','_payment_out','_event_out','_item_out','public_link','_collection','serialize_invoice','log_event','_recalculate_paid'})
ns['datetime']=datetime.datetime;cg.load('backend/app/api/v1/invoices.py',{'ItemIn','InvoiceIn','InvoicePatch'});ns['datetime']=cg.Clock
route_ns=dict(ns,MAX_ITEMS=100,svc=types.SimpleNamespace(**ns))
cg.load('backend/app/api/v1/invoices.py',{'_utc','_get_invoice','_clean_email','_apply_totals','_status_clause','_summary','list_invoices','create_invoice','get_invoice','update_invoice','delete_invoice','duplicate_invoice','void_invoice','list_events','list_payments'},route_ns)
helpers=[]
rng=random.Random(11427)
quantities=[0.0001,0.0005,0.0015,0.9995,1.2345,1.2355,2.675,1.0,2.5,999999.9999]
for n in range(400):
 q=quantities[n%len(quantities)] if n<100 else rng.uniform(0.00001,100)
 # Restrict source products/sums to native source SQL INT storage, not unbounded Python ints.
 unit=rng.randrange(1,1000 if q>100000 else 2000);rate=[0,7.5,2.675,99.995,100,0.005][n%6];items=[dict(description=' Line ',quantity=q,unit_price_minor=unit)];discount=rng.randrange(0,200)
 helpers.append(dict(items=items,discount=discount,rate=rate,expected=ns['calculate_totals'](items,discount_minor=discount,tax_rate=rate)))
rounding=[]
for digits in [0,2,3]:
 for x in [0.0005,0.0015,2.675,99.995,1.2345,1.2355,0.9995,1.5,2.5,math.nextafter(1.5,0),math.nextafter(1.5,2)]+[rng.uniform(0,100000) for _ in range(100)]:rounding.append(dict(input=x,places=digits,expected=round(x,digits)))
ops=[]
def add(kind,**kw):ops.append(dict(kind=kind,**kw))
def body(**kw):return dict(dict(items=[dict(description='Consulting',quantity=1.5,unit_price_minor=10000)],customer_name=' Ada ',tax_rate=7.5),**kw)
add('list');add('create',body=body());add('get',id=1);add('events',id=1);add('payments',id=1);add('list',q={'currency':'NGN'});add('patch',id=1,body={'customer_email':' ada@business.ng ','discount_minor':500});add('patch',id=1,body={'items':[{'description':' Revised ','quantity':2.675,'unit_price_minor':2000}],'tax_rate':2.675,'notes':'Internal'});add('get',id=1);add('duplicate',id=1);add('list',q={'q':'Ada','per_page':1});add('list',q={'q':'no match'});add('list',q={'currency':'USD'});add('list',q={'status':'draft'});add('patch',id=1,body={'due_date':'2025-01-01T00:00:00Z'});add('patch',id=1,body={'customer_email':'bad'});add('patch',id=1,body={'items':[]});add('patch',id=1,body={'status':'paid'});add('create',body=body(items=[]));add('create',body=body(items=[{'description':'Free','unit_price_minor':0}]));add('get',id=404);add('void',id=2);add('list',q={'status':'void'});add('delete',id=1);add('get',id=1);add('create',body=body(currency='USD',customer_name=None,customer_email='email@business.ng',contact_id=1));add('patch',id=3,body={'notes':None,'terms':None,'footer':None,'contact_id':None,'discount_minor':None,'tax_rate':None});add('patch',id=3,body={});add('list',q={'currency':'USD','contact_id':1});add('list',q={'status':'unknown'});add('list',q={'currency':'BOGUS'});add('delete',id=404)
dtos=[]
for patch in (False,True):
 for value in [None,[],{},body(),body(items=None),body(items=[{'description':'X','quantity':None}]),body(items=[{'description':'X','unit_price_minor':None}]),body(items=[{'description':'','quantity':1}]),body(items=[{'description':'X','quantity':0}]),body(items=[{'description':'X','quantity':'1.5','unit_price_minor':'20'}]),body(tax_rate=101),body(discount_minor=-1),body(currency=None),body(customer_email=7),body(issue_date='not a date'),body(customer_name='x'*256),{'items':None,'notes':None}]:
  try:ns['InvoicePatch' if patch else 'InvoiceIn'].model_validate(value);dtos.append(dict(patch=patch,input=value,ok=True))
  except cg.ValidationError:dtos.append(dict(patch=patch,input=value,ok=False))
async def snapshot(db):
 out={}
 fields={'Invoice':['id','number','kind','customer_name','customer_email','customer_phone','contact_id','currency','subtotal_minor','discount_minor','tax_rate','tax_minor','total_minor','amount_paid_minor','status','notes','terms','footer'],'InvoiceItem':['id','invoice_id','description','quantity','unit_price_minor','amount_minor','position'],'InvoiceEvent':['id','invoice_id','event_type','title','body'],'SystemSetting':['key','value','category']}
 for name,keys in fields.items():out[ns[name].__tablename__]=[{k:getattr(r,k) for k in keys} for r in (await db.execute(cg.sa.select(ns[name]).order_by(ns[name].id))).scalars().all()]
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:db.add(ns['Contact'](email='invoice@business.ng'));await db.commit()
 for op in ops:
  async with sessions() as db:
   try:
    kind=op['kind'];kw=dict(db=db,cu=types.SimpleNamespace(username='operator',display_name=None));out=None;status=200
    if kind=='create':out=await route_ns['create_invoice'](body=ns['InvoiceIn'].model_validate(op['body']),**kw);status=201
    elif kind=='patch':out=await route_ns['update_invoice'](invoice_id=op['id'],body=ns['InvoicePatch'].model_validate(op['body']),**kw)
    elif kind=='get':out=await route_ns['get_invoice'](invoice_id=op['id'],**kw)
    elif kind=='duplicate':out=await route_ns['duplicate_invoice'](invoice_id=op['id'],**kw);status=201
    elif kind=='void':out=await route_ns['void_invoice'](invoice_id=op['id'],**kw)
    elif kind=='delete':out=await route_ns['delete_invoice'](invoice_id=op['id'],**kw)
    elif kind=='events':out=await route_ns['list_events'](invoice_id=op['id'],limit=100,**kw)
    elif kind=='payments':out=await route_ns['list_payments'](invoice_id=op['id'],**kw)
    elif kind=='list':
     q=dict(page=1,per_page=20,status=None,currency=None,q=None,contact_id=None,kind=None);q.update(op.get('q',{}));out=await route_ns['list_invoices'](**q,**kw)
    op['expected']=dict(status=status,body=cg.dump(out));await db.commit()
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   except cg.ValidationError:await db.rollback();op['expected']=dict(status=422)
  async with sessions() as db:op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/invoice-source-fixtures.json').write_text(json.dumps(dict(helpers=helpers,rounding=rounding,dto=dtos,operations=ops,scope='Actual selected source AST; fixed clock, deterministic source UUIDs, synthetic SQLite/UTC result adapter, omitted FKs and reconstructed three invoice relationships. No provider imports, original app imports or native financial certification.'),ensure_ascii=False,indent=2)+'\n');print(len(ops),'source invoice operations;',len(helpers),'total cases;',len(rounding),'rounding cases;',len(dtos),'DTO cases')
if __name__=='__main__':asyncio.run(main())
