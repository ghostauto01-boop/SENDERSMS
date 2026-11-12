#!/usr/bin/env python3
import asyncio,base64,csv,io,itertools,json,random,sys
from pathlib import Path
from csv_reference import load
D=Path(__file__).resolve().parents[1];ns=load();u=ns['unicodedata'];schema=D/'sender-suite/schema'
(schema/'csv-aliases.json').write_text(json.dumps(ns['HEADER_ALIASES'],ensure_ascii=False,indent=2)+'\n')
(schema/'unicode15-nfkd-ascii.json').write_text(json.dumps({str(cp):s for cp in range(128,0x110000) if (s:=u.normalize('NFKD',chr(cp)).encode('ascii','ignore').decode())},separators=(',',':'))+'\n')
def b64(b):return base64.b64encode(b).decode()
def parse(b):
 try:return {'rows':list(csv.reader(io.StringIO(b.decode('utf-8-sig',errors='replace'))))}
 except csv.Error:return {'error':'csv_parse'}
reader=[]
for length in range(6):
 for chars in itertools.product('a,\"\n\r',repeat=length):
  b=''.join(chars).encode();reader.append({'bytes':b64(b),'expected':parse(b)})
for s in ['a'*131072,'a'*131073,'😀'*131072,'😀'*131073,'a,b\n"one\r\ntwo",three\n','\0,a\n\0,b\n']:
 b=s.encode();reader.append({'bytes':b64(b),'expected':parse(b)})
inputs=[b'',b'\nName\nAda',b'\xef\xbb\xbfPhone,Email\n08031234567,ada@example.test\n',b'Phone,Email\ninvalid,a@example.test\n0,\n08031234567,\n',b'Email,Email\nfirst@example.test,second@example.test\n',b'"",Email\nignored,ada@example.test,unheaded,tail\n',b'Phone, phone \ninvalid,08031234567\n',b'Name,Email\nAda\n\n""\n',b'Name\n'+b'\n'.join(str(i).encode() for i in range(35)),b'Email\n"a@example.test; b@example.test"',b'Name\n"unterminated\nline',b'Name,Notes\nAda,"one\r\ntwo"\n',b'\xffPhone,Email\n08031234567,\xe0\x80\x80']
for field,aliases in ns['HEADER_ALIASES'].items():
 for alias in aliases:inputs.append((alias+'\n'+('08031234567' if field=='phone_number' else 'ada@example.test' if field=='email' else 'Ada')).encode())
inputs+=['é,É,é,東京,123,!\n1,2,3,4,5,6','\u00a0Phone\u3000,\u001cEmail\u001f\n08031234567,a@example.test']
previews=[]
for i,b in enumerate(inputs):
 if isinstance(b,str):b=b.encode()
 for maximum in ([0,1,20,-1] if i<13 else [20]):
  previews.append({'bytes':b64(b),'max_rows':maximum,'expected':asyncio.run(ns['CSVImportService'](None).preview_csv(b,maximum))})
phones=[None,'','0','08031234567','8031234567','2348031234567','+2348031234567','+14155552671','+442079460018','not-a-number','٠٨٠٣١٢٣٤٥٦٧','８０３１２３４５６７']
import phonenumbers
for region in sorted(phonenumbers.SUPPORTED_REGIONS):
 example=phonenumbers.example_number(region)
 if example:phones.append(phonenumbers.format_number(example,phonenumbers.PhoneNumberFormat.E164))
phone=[{'input':s,'expected':ns['clean_phone'](s)} for s in phones]
keys=[{'input':chr(cp),'expected':ns['custom_field_key'](chr(cp))} for cp in range(128,0x110000) if u.normalize('NFKD',chr(cp)).encode('ascii','ignore')]
keys+=[{'input':s,'expected':ns['custom_field_key'](s)} for s in ['東京','🦄','123 Lead Value','\ufeffPhone','', '０']]
rng=random.Random(471);byte_inputs=[bytes([i]) for i in range(256)]+[bytes([lead,tail]) for lead in [0xc0,0xc2,0xdf,0xe0,0xed,0xef,0xf0,0xf4,0xf5] for tail in range(256)]+[bytes(rng.randrange(256) for _ in range(rng.randrange(1,10))) for i in range(1000)]+[b'\xef\xbb\xbfabc',b'\xef\xbb\xbf\xef\xbb\xbf']
decodes=[{'bytes':b64(b),'expected':b.decode('utf-8-sig',errors='replace')} for b in byte_inputs]
result={'reader':reader,'previews':previews,'phone':phone,'keys':keys,'decodes':decodes,'scope':'Fresh original source AST comparisons, synthetic/public example inputs; no app/config/providers'}
(D/'tests/csv-source-fixtures.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n');print({k:len(v) for k,v in result.items() if isinstance(v,list)})
