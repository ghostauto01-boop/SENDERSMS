#!/usr/bin/env python3
"""Selected original synchronous import AST; fixed-clock isolated SQLite, no app/config imports or sends."""
import ast,asyncio,base64,csv,datetime,importlib.util,io,json,types,typing
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('offline_variables_reference',D/'tools/generate_variable_fixtures.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);cg=v.cg;ns=v.ns
import unicodedata2
ns.update(csv=csv,io=io,unicodedata=unicodedata2,Index=cg.sa.Index,IntegrityError=cg.sa.exc.IntegrityError,Callable=typing.Callable,Awaitable=typing.Awaitable)
def literal(path,name):
 for n in ast.parse((ROOT/path).read_text()).body:
  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets):return ast.literal_eval(n.value)
  if isinstance(n,ast.AnnAssign) and getattr(n.target,'id','')==name:return ast.literal_eval(n.value)
 raise RuntimeError('Source literal missing '+name)
for name in ('CONTACT_FIELDS','TAGS_FIELD','HEADER_ALIASES','_COUNTRY_BY_TLD'):ns[name]=literal('backend/app/services/csv_service.py',name)
ns['FREE_MAIL_DOMAINS']=literal('backend/app/services/email_enrichment.py','FREE_MAIL_DOMAINS')
(D/'sender-suite/schema/csv-import-data.json').write_text(json.dumps(dict(countries=ns['_COUNTRY_BY_TLD'],free_mail_domains=sorted(ns['FREE_MAIL_DOMAINS'])),ensure_ascii=False,indent=2)+'\n')
cg.load('backend/app/models/email_inbox.py',{'_now','EmailContactAddress'},models=True);ns['EmailContactAddress'].__table__.dialect_options['sqlite']['autoincrement']=True
cg.load('backend/app/utils/contact_identity.py',{'contact_key'});cg.load('backend/app/services/email_service.py',{'remember_contact_email'});cg.load('backend/app/services/email_enrichment.py',{'domain_from_website','store_secondary_email'})
cg.load('backend/app/services/csv_service.py',{'extract_emails','split_tags','custom_field_key','infer_country','detect_column_mapping','CSVImportResult','CSVImportService'})
class Upload:
 def __init__(self,b):self.b=b
 async def read(self,n):return self.b[:n]
ns.update(UploadFile=Upload,File=lambda *a,**k:None,Form=lambda default=None,**k:default,MAX_CONTACT_IMPORT_BYTES=20*1024*1024)
cg.load('backend/app/api/v1/contacts.py',{'_parse_import_json','preview_csv_import','import_csv'})
for name in ('Contact','Tag','ContactTag','ContactList','ContactListMember'):ns[name].__table__.dialect_options['sqlite']['autoincrement']=True
helpers=[]
for raw in [None,'','Ada <Ada@business.ng>; billing@business.ng','A@x.com A@x.com; b@x.com','bad email','0','😀@business.ng','<x@business.ng>,','\u001cfirst@x.com\u001esecond@x.com']:
 helpers.append(dict(kind='emails',input=raw,expected=ns['extract_emails'](raw)))
for raw in [None,'','Buyer; buyer|VIP,Buyer','0','x'*101,'Buyer\nVIP','é'*100]:helpers.append(dict(kind='tags',input=raw,expected=ns['split_tags'](raw)))
for raw in [None,'','https://www.shop.co.uk/path','shop.ng','user:pass@shop.ng:44/a','//www.business.ng/path','http://foo.ng:bad','http://[::1]/','http://[broken','foo//bar.ng','https://shop.ng?x=1','https://foo\t.ng/a']:
 helpers.append(dict(kind='domain',input=raw,expected=ns['domain_from_website'](raw)))
for country,email,website in [(None,'a@business.ng',None),(' UK ',None,None),(None,'a@gmail.com','shop.co.uk'),(None,'a@business.fr','shop.ng'),(None,None,'shop.unknown'),('é'*101,None,None)]:helpers.append(dict(kind='country',input=[country,email,website],expected=ns['infer_country'](country,email,website)))
ops=[]
def add(text,**options):ops.append(dict(csv=base64.b64encode(text.encode()).decode(),options=options))
add('Name,Phone,Email,Company,Pain Point,Tags\nAda,08031234567,ada@business.ng,Acme,time,Buyer|VIP\nOnly Email,,hello@other.co.uk,Other,price,Lead\nBad,nope,,,,\nEmpty,,,,,\n',new_list_name=' First batch ',tags='Global;Buyer',auto_tag_rules=json.dumps([{'field':'Pain Point','contains':'time','tag':'Busy'}]))
add('Phone,Email,City,Pain Point,Extra\n08031234567,other@business.ng,Lagos,new pain,New fact\n08031234567,ignored@business.ng,Abuja,,\n',list_id=1)
ops[-1]['fixture_opt_out']=True
add('Email,Notes\nother@business.ng,matched alias\n',list_id=1)
add('Name,Email\nCompany friend,friend@business.ng\n',list_id=1)
add('First Name,Last Name,Phone\nChi,Okafor,08051234567\n')
add('First Name,Last Name,Email\nChi,Okafor,chi@business.ng\n')
add('First Name,Last Name,Email\nSame,Name,one@different.ng\nSame,Name,two@different.ng\n')
add('First Name,Last Name,Phone\nSame,Name,08091234567\n')
add('Email,Country,Website\na@unknown.test,,https://shop.fr/\nb@unknown.test,Kenya,https://shop.ng/\n',skip_duplicates=False)
add('Email,Other Emails,Ignore\nfirst@business.ng;second@business.ng,value,secret\n',column_mapping=json.dumps({'Other Emails':'custom:Lead Value','Ignore':''}),tags='Lower|lower')
add('Email\nsolo@business.ng\nsolo@business.ng\n',skip_duplicates=False)
add('Email,Extra\nsolo@business.ng,filled\n',new_list_name='Second')
add('Email\n0\nnot mail\n')
add('Email\nx@business.ng\n',list_id=404)
add('Email\nx@business.ng\n',new_list_name='Rollback list',column_mapping='{')
add('Email\nx@business.ng\n',column_mapping='[]')
add('Email\nx@business.ng\n',auto_tag_rules='{}')
add('Email\nx@business.ng\n',auto_tag_rules='{')
add('',new_list_name='No headers rollback')
add('Email,Phone\nx@business.ng,invalid\n')
add('Phone,Email\n08031234567,other@business.ng\n')
add('Email,Email\nignored@business.ng,kept@business.ng\n')
add(' Email ,Mood\nspace@business.ng,0\n',column_mapping=json.dumps({'Mood':'custom:mood'}))
async def snapshot(db):
 fields=['id','first_name','last_name','business_name','phone_number','email','email_lower','email_source','country','website','city','notes','custom_fields','has_consented','is_opted_out','is_email_opted_out','lead_status']
 rows=(await db.execute(cg.sa.select(ns['Contact']).order_by(ns['Contact'].id))).scalars().all();contacts=[{k:getattr(c,k) for k in fields} for c in rows]
 def payload(row,keys):return {k:getattr(row,k) for k in keys}
 out={'contacts':contacts}
 for key,model,fields in [('tags','Tag',['id','name']),('tag_links','ContactTag',['contact_id','tag_id']),('lists','ContactList',['id','name','contact_count']),('members','ContactListMember',['list_id','contact_id']),('aliases','EmailContactAddress',['contact_id','email_address'])]:out[key]=[payload(r,fields) for r in (await db.execute(cg.sa.select(ns[model]).order_by(ns[model].id))).scalars().all()]
 return out
async def main():
 engine=cg.create_async_engine('sqlite+aiosqlite:///:memory:')
 async with engine.begin() as con:await con.run_sync(cg.Base.metadata.create_all)
 sessions=cg.async_sessionmaker(engine,expire_on_commit=False)
 async with sessions() as db:
  for op in ops:
   if op.get('fixture_opt_out'):
    contact=await db.get(ns['Contact'],1);contact.is_opted_out=True;contact.is_email_opted_out=True;await db.commit()
   try:
    out=await ns['import_csv'](file=Upload(base64.b64decode(op['csv'])),**op['options'],db=db,current_user=None);await db.commit();op['expected']=dict(status=200,body=cg.dump(out))
   except cg.HTTPException as e:await db.rollback();op['expected']=dict(status=e.status_code,message=e.detail)
   op['snapshot']=await snapshot(db)
 await engine.dispose()
 (D/'tests/import-source-fixtures.json').write_text(json.dumps(dict(helpers=helpers,operations=ops,scope='Selected synchronous original CSV API/service and alias helpers; fixed clock, synthetic SQLite, monotonic committed ids, omitted relationships; no actual upload/WP/MySQL/concurrency/job/URL/DNS/provider certification'),ensure_ascii=False,indent=2)+'\n');print(len(helpers),'helper cases;',len(ops),'source import operations with full state snapshots')
if __name__=='__main__':asyncio.run(main())
