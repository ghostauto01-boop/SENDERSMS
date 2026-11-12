#!/usr/bin/env python3
"""Actual selected source exports with isolated SQLite; CSV safety adjustment is explicitly separate."""
import asyncio,base64,csv,hashlib,importlib.util,io,json,re
from pathlib import Path
D=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('export_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg);ns=cg.ns
class Stream:
 def __init__(self,content,**kw):self.body=''.join(content)
ns.update(base64=base64,csv=csv,hashlib=hashlib,io=io,json=json,StreamingResponse=Stream,EXPORT_COLUMNS=['first_name','last_name','business_name','phone_number','email','city','state','country','website','industry','source','lead_status','notes'])
cg.load('backend/app/api/v1/contacts.py',{'_encode_cursor','_decode_cursor','_custom_values','_tag_map','_export_row','_export_query','export_contacts_page','export_csv'})
def safe_csv(raw):
 out=io.StringIO();writer=csv.writer(out)
 for row in csv.reader(io.StringIO(raw)):
  writer.writerow(["'"+v if re.match(r'^[\x00-\x20]*[=+@-]',v) or v.startswith(('\t','\r','\n')) else v for v in row])
 return out.getvalue()
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False);seeds=[];ops=[]
 async with sessions() as db:
  async def seed(model,**values):
   row=ns[model](**values);db.add(row);await db.flush();seeds.append(dict(table=row.__tablename__,values={c.name:cg.dump(getattr(row,c.name)) for c in row.__table__.columns}));return row
  contacts=[dict(first_name='Ada',email='ada@business.ng',phone_number='+2348031112222',city='Abuja',custom_fields=json.dumps({'tier':'Gold','score':42,'enabled':False,'nested':{'x':True}})),dict(first_name='Bola',email='bola@business.ng',notes='A comma, a quote " and\na newline',custom_fields=json.dumps({'tier':'Silver','note':'é 😀','items':[1,True,None]})),dict(first_name='=HYPERLINK("https://evil.invalid")',email='safe@business.ng',notes='\t=1+1',custom_fields=json.dumps({'=header':'@SUM(1)','0':'zero'})),dict(first_name='No email',phone_number='+2348031112223',custom_fields='not json'),dict(first_name='Last',email='last@business.ng',notes='x'*1100,custom_fields=json.dumps({'later':'value'}))]
  for i,v in enumerate(contacts):await seed('Contact',**v,created_at=cg.NOW.replace(microsecond=i))
  await seed('Tag',name='Buyer');await seed('ContactTag',contact_id=1,tag_id=1);await seed('ContactTag',contact_id=2,tag_id=1);await seed('ContactList',name='Subset');await seed('ContactListMember',list_id=1,contact_id=2);await seed('ContactListMember',list_id=1,contact_id=5);await db.commit()
  default=dict(search=None,lead_status=None,tag=None,email_state=None,channel=None,list_id=None)
  async def add(filters=None,**q):
   f={**default,**(filters or {})};kind=q.pop('kind','page');kw=dict(db=db,current_user=None,**f)
   if kind=='download':body=(await ns['export_csv'](**kw)).body;expected={'csv':body,'safe_csv':safe_csv(body)}
   else:
    try:
     body=await ns['export_contacts_page'](cursor=q.get('cursor'),limit=q.get('limit',200),max_bytes=q.get('max_bytes',20000),format=q.get('format','csv'),**kw);expected=dict(status=200,body=body)
     if 'csv' in body:expected['safe_csv']=safe_csv(body['csv'])
    except cg.HTTPException as e:expected=dict(status=e.status_code,message=e.detail)
   ops.append(dict(kind=kind,filters=f,query=q,expected=expected));return expected.get('body',{})
  for filters in ({},{'search':'Bola'},{'tag':'Buyer'},{'channel':'sms'},{'channel':'email'},{'list_id':1},{'search':'missing'},{'search':'é'}):
   for fmt in ('csv','json'):await add(filters,format=fmt,limit=2)
   await add(filters,kind='download')
  for fmt in ('csv','json'):
   cursor=None
   while True:
    body=await add(format=fmt,limit=2,cursor=cursor,max_bytes=1000);cursor=body.get('next_cursor')
    if not cursor:break
  page=await add(limit=1);token=page['next_cursor'];await add({'channel':'email'},cursor=token);await add(cursor='not-a-cursor');await add(cursor=ns['_encode_cursor']({'last_id':0,'columns':[],'f':'wrong'}))
 await engine.dispose()
 (D/'tests/export-source-fixtures.json').write_text(json.dumps(dict(seeds=seeds,operations=ops,scope='Actual selected source exports, synthetic SQLite, no native WordPress/MySQL/browser evidence. Raw source CSV retained; safe_csv separately neutralizes dangerous spreadsheet cells. Native UTF-8 byte budgets and cursor/column/resource hardening are separately tested intentional differences.'),indent=2,ensure_ascii=False)+'\n');print(len(ops),'source export operations')
if __name__=='__main__':asyncio.run(main())
