#!/usr/bin/env python3
"""Require structured PHP success: WASM can exit0 after a fatal. Original app imports/egress denied."""
import hashlib,json,os,subprocess
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;cli=D/'tooling/node_modules/.bin/php-wasm-cli';records=[];lint=0
for version in ('7.4','8.3'):
 env=dict(os.environ,PHP=version,NODE_OPTIONS='--require='+str(D/'tools/deny-network.cjs'))
 for file in sorted((D/'sender-suite').rglob('*.php')):
  if 'vendor' in file.relative_to(D/'sender-suite').parts:continue
  p=subprocess.run([str(cli),'-l',str(file)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
  if p.returncode or 'No syntax errors detected' not in p.stdout:raise RuntimeError('PHP syntax failed: '+str(file))
  lint+=1
 for suite in ('core','csv','hooks','auth','auth-routes','contacts','data-routes','variables','templates','reply','import','import-jobs','url-import','analytics','sequences','contact-actions','exports','runtime-settings','calendar','invoices','inbox','sms-receipts','followups','access-profile','dashboard','campaign-reports','sms-gateway','sms-outbox','sms-outbox-routes','vendor-parse'):
  p=subprocess.run([str(cli),str(D/'tests'/f'{suite}-test.php')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
  try:record=json.loads(p.stdout)
  except json.JSONDecodeError:raise RuntimeError('No structured PHP success for '+suite+' '+version+'; stdout: '+p.stdout[:500])
  if p.returncode or record.get('ok') is not True:raise RuntimeError('PHP test failed '+suite+' '+version+': '+json.dumps(record)[:2000])
  record['suite']=suite;(D/'tests'/f'{suite}-php{version.replace(".","")}.json').write_text(json.dumps(record,indent=2)+'\n');records.append(record)
hashes=json.loads((D/'sender-suite/schema/frontend-hashes.json').read_text());assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items()),'Original frontend changed'
manifest=json.loads((D/'sender-suite/schema/phone-vendor-manifest.json').read_text());vendor_checks=0
for package,folder in zip(manifest,('phone','locale','mbstring')):
 for file,digest in package['files'].items():
  assert hashlib.sha256((D/'sender-suite/vendor'/folder/file).read_bytes()).hexdigest()==digest,'Vendor file changed';vendor_checks+=1
summary={'ok':True,'php_lint_checks':lint,'php_assertions':sum(r['assertions'] for r in records if r['suite']!='vendor-parse'),'vendor_parser_checks':sum(r['assertions'] for r in records if r['suite']=='vendor-parse'),'vendor_hash_checks':vendor_checks,'unchanged_frontend_files':len(hashes),'records':records,'native_mysql_verified':False,'native_wordpress_install_verified':False,'original_app_http_contracts_verified':False,'browser_workflows_verified':False}
(D/'tests/checks-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
