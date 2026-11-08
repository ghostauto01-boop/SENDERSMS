#!/usr/bin/env python3
"""Real HTTP against an isolated CI site. No provider messages or external requests."""
import http.cookiejar,json,os,subprocess,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
E=Path(os.environ['NATIVE_WP_EVIDENCE']);BASE='http://127.0.0.1:8080';checks=0;failures=[]
cookie_roundtrip=False
jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));anonymous=urllib.request.build_opener();private=json.loads((E/'private-test-cookie.json').read_text())
def check(label,actual,expected=True):
 global checks
 if actual!=expected:failures.append(dict(label=label,actual=actual,expected=expected))
 else:checks+=1

def request(method,path,body=None,query=None,headers=None,authenticated=True):
 params={'rest_route':'/sender-suite/v1/'+path};params.update(query or {});hdr={'Accept':'application/json'};hdr.update(headers or {})
 data=None if body is None else json.dumps(body).encode()
 if data is not None:hdr['Content-Type']='application/json'
 req=urllib.request.Request(BASE+'/?'+urllib.parse.urlencode(params),data=data,headers=hdr,method=method)
 try:r=(client if authenticated else anonymous).open(req,timeout=30)
 except urllib.error.HTTPError as e:r=e
 raw=r.read();payload=json.loads(raw) if raw else None
 return r.status,payload,r.headers

def upload(path,content,fields=None,authenticated=True):
 boundary='SenderSuiteIsolatedMultipartBoundary'
 parts=[]
 for key,value in (fields or {}).items():parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
 parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="contacts.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()+content+f'\r\n--{boundary}--\r\n'.encode())
 req=urllib.request.Request(BASE+'/?'+urllib.parse.urlencode({'rest_route':'/sender-suite/v1/'+path}),data=b''.join(parts),headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
 try:r=(client if authenticated else anonymous).open(req,timeout=30)
 except urllib.error.HTTPError as e:r=e
 return r.status,json.loads(r.read()),r.headers

def raw_report(job):
 req=urllib.request.Request(BASE+'/?'+urllib.parse.urlencode({'rest_route':f'/sender-suite/v1/contacts/import/jobs/{job}/errors.csv','_envelope':'1','_jsonp':'evil','_fields':'body'}))
 try:r=client.open(req,timeout=30)
 except urllib.error.HTTPError as e:r=e
 return r.status,r.read(),r.headers

try:
 # Bounded startup backoff in the CI test, not a sandbox dev-server poller.
 for attempt in range(20):
  try:urllib.request.urlopen(BASE,timeout=2).read();break
  except urllib.error.URLError:time.sleep(.25)
 s,b,h=request('GET','health');check('native health',s,200);check('health payload',b,{'status':'ok','app':'Sendify','version':'1.0.0'})
 s,b,h=request('GET','contacts');check('anonymous own-app access denied',s,401)
 s,b,h=request('POST','templates/',{'name':'No session'},headers={'Cookie':private['name']+'='+private['value']},authenticated=False);check('real WordPress admin cookie cannot bypass app login',s,401)
 s,b,h=request('POST','auth/login',{'username':'native_operator','password':'WRONG'});check('bad login',s,401)
 s,b,h=request('POST','auth/login',{'username':'native_operator','password':'Isolated-operator-test-only-2357'});check('actual HTTP Argon2id login',s,200);check('login body does not expose JWT','token' not in b);check('HttpOnly cookie','HttpOnly' in h.get('Set-Cookie',''));check('SameSite cookie','samesite=lax' in h.get('Set-Cookie','').lower());check('native cookie jar populated',any(c.name=='sendsms_session' for c in jar));check('login no-store',h.get('Cache-Control'),'no-store')
 s,b,h=request('GET','auth/me');check('native cookie roundtrip',s,200);cookie_roundtrip=s==200 and b.get('username')=='native_operator';check('operator username',b.get('username'),'native_operator')
 s,b,h=request('POST','contacts/',{'first_name':'Ada','email':'<Ada@business.ng>,','tags':'Buyer,buyer'});check('native contact create',s,201);contact=b.get('id');check('native email normalization',b.get('email'),'ada@business.ng');check('native tag deduplication',b.get('tags'),['Buyer']);check('native internal identity hidden','email_lower' not in b)
 s,b,h=request('POST','contacts/',{'first_name':'Other','email':'ADA@business.ng'});check('native contact duplicate merge',s,201);check('native duplicate keeps ID',b.get('id'),contact)
 s,b,h=request('PUT','contacts/'+str(contact),{'first_name':'Ada'});check('native contact update',s,200)
 s,b,h=request('GET','contacts',query={'search':'ada','page':1,'per_page':25});check('native contact filtering',b.get('total'),1)
 s,b,h=request('POST','contacts/',{'email':'forbidden@business.ng'},headers={'Origin':'https://evil.example'});check('cross-origin native write refused',s,403)
 s,b,h=request('POST','lists/','ignored JSON name',{'name':'Native list'});check('native list query create',s,201);list_id=b.get('id')
 s,b,h=request('POST',f'lists/{list_id}/contacts',[contact,contact]);check('native membership write',s,200);check('native membership deduplication',b.get('added'),1)
 s,b,h=request('POST',f'lists/{list_id}/contacts',[contact]);check('native membership idempotency',b.get('added'),0)
 s,b,h=request('GET',f'lists/{list_id}/contacts');check('native members',b.get('total'),1)
 s,b,h=request('POST','variables/sync');check('native variable sync',s,200);check('native variable builtins',b.get('total'),11)
 s,b,h=request('POST','variables/',{'field_key':'Pain Point','fallback_text':'More time'});check('native custom registry entry',s,201);variable=b.get('id')
 s,b,h=request('PUT','variables/'+str(variable),{'shortcode':'pain'});check('native shortcode update',b.get('shortcode'),'pain')
 s,b,h=request('POST','variables/preview',query={'body':'Hi {{pain}}','contact_id':contact});check('native registry preview',b.get('preview'),'Hi More time')
 s,b,h=request('POST','templates/',{'name':'SMS','body':'Hi {{first_name}}','category':'Native'});check('native SMS template create',s,201);sms=b.get('id');check('native Integer/True factory',b.get('is_active'),True);check('native SMS metrics',b.get('char_count'),17)
 blob='AAECAwQFBgcICQ=='
 s,b,h=request('POST','templates/',{'name':'Email','channel':'email','subject':'Hi','body':'Plain','attachments':[{'name':'sample.bin','content':blob}]});check('native email template create',s,201);email=b.get('id');check('native attachment size',b.get('attachments',[{}])[0].get('size'),10);check('native attachment payload hidden',blob not in json.dumps(b))
 s,b,h=request('GET','templates/',query={'channel':'email'});check('native channel filtering',b.get('total'),1);check('native list hides attachment bytes',blob not in json.dumps(b))
 s,b,h=request('POST','templates/',{'name':'Bad','body':'{{frist_name}}'});check('native unknown variable error',s,422);check('native structured error code',b.get('code'),'UNKNOWN_TEMPLATE_VARIABLES');check('native typo suggestions',b.get('suggestions'),{'frist_name':'first_name'});check('native request ID correlation',b.get('request_id'),h.get('X-Request-ID'))
 s,b,h=request('GET','templates/');check('native failed template write rolled back',b.get('total'),2)
 s,b,h=request('PUT',f'templates/{sms}',{'is_active':False,'channel':'email'});check('native template deactivation',b.get('is_active'),False);check('native immutable template channel',b.get('channel'),'sms')
 s,b,h=request('POST',f'templates/{sms}/duplicate');check('native duplicate route',s,200);check('native duplicate resets active flag',b.get('is_active'),True)
 s,b,h=request('POST',f'templates/{sms}/to-email');check('native conversion route',s,200);check('native conversion channel',b.get('channel'),'email')
 s,b,h=request('POST','templates/preview',query={'body':'Hello {{first_name}}','contact_id':contact});check('native template preview',b.get('preview'),'Hello Ada');check('native preview actual contact',b.get('sample'),False)
 s,b,h=request('GET','templates/'+str(email),query={'_fields':'name','_envelope':'1','_jsonp':'evil','_method':'DELETE'});check('WP reserved flags cannot change source method/shape',s,200);check('WP fields/envelope bypass disabled','id' in b and 'body' in b and 'attachments' in b);check('WP JSONP disabled','javascript' not in h.get('Content-Type',''))
 s,b,h=request('DELETE','templates/'+str(email));check('native template delete',s,204);check('native 204 empty body',b,None)
 s,b,h=request('GET','templates/'+str(email));check('native deleted template missing',s,404)
 # Actual SAPI multipart uploads and actual MySQL worker execution, no synthetic reader.
 s,b,h=upload('contacts/import/jobs',b'Email\nno-session@business.ng\n',authenticated=False);check('native anonymous upload denied',s,401)
 s,b,h=upload('contacts/import/preview',b'Email,Import Note\npreview-only@business.ng,preview\n');check('native multipart preview',s,200);check('native multipart preview rows',b.get('total_rows'),1)
 s,b,h=request('GET','contacts',query={'search':'preview-only'});check('native preview no contact writes',b.get('total'),0)
 s,b,h=upload('contacts/import/csv',b'Email,Import Note\nsynchronous@business.ng,sync\n',{'new_list_name':'Native synchronous CSV'});check('native synchronous upload import',s,200);check('native synchronous contact persisted',b.get('imported'),1)
 data='Email,Phone,Import Note\n'+''.join(f'native-job-{i}@business.ng,,value\n' for i in range(105))+',bad,invalid\n'
 s,b,h=upload('contacts/import/jobs',data.encode(),{'new_list_name':'Native queued CSV','tags':'NativeJob'});check('native queued upload accepted',s,202);job=b.get('id');check('native job initially queued',b.get('status'),'queued');check('native job row total',b.get('total_rows'),106);check('native job never returns raw CSV','content' not in b)
 s,b,h=request('GET',f'contacts/import/jobs/{job}');check('native job poll before worker',s,200);check('native no implicit processing on poll',b.get('processed_rows'),0)
 worker=subprocess.run(['php',str(Path(__file__).with_name('native-import-worker.php'))],capture_output=True,text=True,timeout=90)
 check('native worker exits successfully',worker.returncode,0);check('native worker structured success',json.loads(worker.stdout).get('ok'),True)
 s,b,h=request('GET',f'contacts/import/jobs/{job}');check('native queued job completed',b.get('status'),'completed');check('native final job progress',b.get('progress_percent'),100);check('native final processed rows',b.get('processed_rows'),106);check('native job imported count',b.get('result',{}).get('imported'),105);check('native job invalid count',b.get('result',{}).get('invalid'),1);check('native job live list count',b.get('list',{}).get('contact_count'),105);check('native job omits imported IDs','imported_ids' not in b.get('result',{}))
 s,raw,h=raw_report(job);check('native raw error report',s,200);check('native CSV not JSON wrapped',raw.startswith(b'row,level,error\r\n'));check('native report has phone warning',b'Phone number skipped' in raw);check('native report has invalid row',b'No phone number and no email' in raw);check('native error report content type',h.get('Content-Type'),'text/csv; charset=UTF-8');check('native error report cache private',h.get('Cache-Control'),'no-store');check('native error report attachment',h.get('Content-Disposition'),f'attachment; filename="import-errors-{job}.csv"')
 s,b,h=request('POST','auth/logout');check('native logout',s,200)
 s,b,h=request('GET','contacts');check('native logout clears browser cookie',s,401)
except Exception as e:failures.append({'exception':type(e).__name__,'message':str(e)})
finally:
 result=dict(ok=not failures,assertions=checks,failures=failures,native_wordpress_http=True,native_mysql=True,actual_http_cookie_roundtrip=cookie_roundtrip,provider_messages_sent=0,complete_application=False,production_ready=False,scope='Actual HTTP/WordPress/MySQL smoke checks for implemented auth/contact/list/variable/template/synchronous import/job/CSV report paths and explicit native import worker (not actual WP cron dispatch); not full contract/SPA/browser/native concurrency/destructive FK/migration/provider certification')
 (E/'native-http.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));(E/'private-test-cookie.json').unlink(missing_ok=True)
raise SystemExit(bool(failures))
