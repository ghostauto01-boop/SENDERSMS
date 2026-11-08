#!/usr/bin/env python3
"""Write current, honest restoration ledger and evidence derived from fresh local test output."""
import collections,hashlib,json,re
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
endpoints=json.loads((D/'sender-suite/schema/endpoints.json').read_text());pairs=collections.defaultdict(list)
for e in endpoints:pairs[(e['method'],e['path'])].append(e)
partial={('GET','/api/v1/health'),('GET','/api/v1/auth/access'),('POST','/api/v1/auth/admin'),('POST','/api/v1/auth/login'),('POST','/api/v1/auth/logout'),('GET','/api/v1/auth/me')}
partial.update({('GET','/api/v1/contacts'),('GET','/api/v1/contacts/'),('POST','/api/v1/contacts/'),('GET','/api/v1/contacts/{contact_id}'),('PUT','/api/v1/contacts/{contact_id}'),('GET','/api/v1/contacts/tags/'),('GET','/api/v1/lists'),('GET','/api/v1/lists/'),('POST','/api/v1/lists/'),('PUT','/api/v1/lists/{list_id}'),('GET','/api/v1/lists/{list_id}/contacts'),('POST','/api/v1/lists/{list_id}/contacts'),('POST','/api/v1/lists/{list_id}/contacts/add-all'),('POST','/api/v1/lists/{list_id}/contacts/remove'),('GET','/api/v1/lists/{list_id}/stats')})
partial.update({('GET','/api/v1/variables'),('GET','/api/v1/variables/'),('POST','/api/v1/variables/'),('PUT','/api/v1/variables/{variable_id}'),('DELETE','/api/v1/variables/{variable_id}'),('POST','/api/v1/variables/sync'),('POST','/api/v1/variables/preview')})
partial.update({('GET','/api/v1/templates'),('GET','/api/v1/templates/'),('POST','/api/v1/templates/'),('GET','/api/v1/templates/categories'),('POST','/api/v1/templates/preview'),('GET','/api/v1/templates/{template_id}'),('PUT','/api/v1/templates/{template_id}'),('DELETE','/api/v1/templates/{template_id}'),('POST','/api/v1/templates/{template_id}/duplicate'),('POST','/api/v1/templates/{template_id}/to-email')})
for root,key in [('autoreply','rule_id'),('automations','automation_id')]:
 partial.update({('GET',f'/api/v1/{root}'),('GET',f'/api/v1/{root}/'),('POST',f'/api/v1/{root}/'),('GET',f'/api/v1/{root}/{{{key}}}'),('PUT',f'/api/v1/{root}/{{{key}}}'),('DELETE',f'/api/v1/{root}/{{{key}}}'),('POST',f'/api/v1/{root}/test')})
partial.update({('POST','/api/v1/ai/classify'),('GET','/api/v1/notifications'),('GET','/api/v1/notifications/'),('GET','/api/v1/notifications/unread-count'),('POST','/api/v1/notifications/{event_id}/read'),('POST','/api/v1/notifications/read-all')})
partial.update({('POST','/api/v1/contacts/import/preview'),('POST','/api/v1/contacts/import/csv')})
partial.update({('POST','/api/v1/contacts/import/jobs'),('GET','/api/v1/contacts/import/jobs/{job_id}'),('GET','/api/v1/contacts/import/jobs/{job_id}/errors.csv')})
partial.add(('POST','/api/v1/contacts/import/url'))
partial.update({('GET','/api/v1/analytics/'+path) for path in ('overview','timeseries','funnel','by-campaign','calls')})
partial.update({('GET','/api/v1/sequences'),('GET','/api/v1/sequences/'),('POST','/api/v1/sequences/'),('GET','/api/v1/sequences/{sequence_id}'),('PUT','/api/v1/sequences/{sequence_id}'),('DELETE','/api/v1/sequences/{sequence_id}')})
partial.update({('DELETE','/api/v1/contacts/{contact_id}'),('POST','/api/v1/contacts/bulk'),('POST','/api/v1/contacts/clean'),('POST','/api/v1/contacts/quarantine/generated'),('POST','/api/v1/contacts/{contact_id}/email-opt-out'),('POST','/api/v1/contacts/{contact_id}/email-opt-in'),('POST','/api/v1/contacts/{contact_id}/sms-opt-in'),('GET','/api/v1/contacts/{contact_id}/activity'),('DELETE','/api/v1/lists/{list_id}'),('POST','/api/v1/lists/{list_id}/contacts/delete'),('POST','/api/v1/lists/{list_id}/clean')})
partial.update({('GET','/api/v1/contacts/export'),('GET','/api/v1/contacts/export/csv')})
assert partial.issubset(pairs), 'A reported partial path is absent from the source inventory'

contracts=[{'method':method,'path':path,'sources':[r['source'] for r in rows],'state':'partial handler; native WordPress/HTTP unverified' if (method,path) in partial else 'missing original application handler','certified':False} for (method,path),rows in sorted(pairs.items())]
assert len(contracts)==508
assert sum(c['state'].startswith('partial') for c in contracts)==88
(D/'docs/contracts.json').write_text(json.dumps(contracts,indent=2)+'\n')
text='# Remaining WordPress recreation work\n\n420 missing source handlers,88 partial health/auth/contact/list/variable/template/reply/classification/notification/import/analytics/sequence handlers,0 fully certified contracts. The administrator CSV diagnostic is not the original source import API.\n\nEvery row is outstanding, including all partial handlers until native HTTP certification. See README.md for actual implemented utilities and fresh test boundaries.\n\n| Method | Path | Source | State |\n|---|---|---|---|\n'
for c in contracts:text+='| '+c['method']+' | '+c['path'].replace('|','\\|')+' | '+', '.join(c['sources'])+' | '+c['state']+' |\n'
(D/'docs/REMAINING_WORK.md').write_text(text)
log=D/'.cache/frontend-test.log'
if log.is_file():
 raw=log.read_bytes();clean=re.sub(r'\x1b\[[0-9;]*m','',raw.decode());tests=re.search(r'Tests\s+(\d+) passed',clean);files=re.search(r'Test Files\s+(\d+) passed',clean)
 if not tests or not files or int(tests[1])!=269 or int(files[1])!=43:raise RuntimeError('Frontend evidence missing; do not reuse historical results')
 build=(D/'.cache/frontend-build.log').read_text();assert 'built in' in build
 record={'test_command':'npm test -- --reporter=dot','tests_passed':int(tests[1]),'test_files_passed':int(files[1]),'test_output_sha256':hashlib.sha256(raw).hexdigest(),'build_command':'npm run build -- --outDir ../wordpress/sender-suite/app','build_passed':True,'built_asset_files':sum(p.is_file() for p in (D/'sender-suite/app').rglob('*')),'original_frontend_modified':False,'native_wordpress_browser_verified':False,'note':'Fresh stdout from actual execution, not earlier conversation counts; jsdom navigation warning is not browser coverage'}
 (D/'tests/frontend-evidence.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'source_contracts':len(contracts),'missing_handlers':420,'partial_handlers':88,'certified':0}))
