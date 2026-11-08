#!/usr/bin/env python3
"""Execute actual pure source number filtering AST; never import deployment settings or send anything."""
import ast,dataclasses,importlib.util,json
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent
spec=importlib.util.spec_from_file_location('number_reference',D/'tools/generate_contact_fixtures.py');cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg);ns=cg.ns
ns.update(dataclass=dataclasses.dataclass,field=dataclasses.field)
for path,names in [('backend/app/utils/phone.py',{'NG_MOBILE_PREFIXES'}),('backend/app/services/number_filter.py',{'_EXAMPLE_LOCAL','_SEQUENTIAL_FULL'})]:
 nodes=[n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.Assign) and any(getattr(t,'id',None) in names for t in n.targets)]
 exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'source-number-constants','exec'),ns)
cg.load('backend/app/utils/phone.py',{'is_nigerian_mobile'})
cg.load('backend/app/services/number_filter.py',{'FilterResult','FilterReport','_national10','_is_hard_fake','looks_like_test_data','classify_number','filter_numbers'})
phones=[v['input'] for v in json.loads((D/'tests/csv-source-fixtures.json').read_text())['phone']]
phones+=['08031111111','08033333333','08123456789','08000000000','09012345678','08888888888','+12025550123','123','12345678','9876543210',None,'+٢٣٤٨٠٣١١١٢٢٢٢','٠٨٠٣١١١٢٢٢٢','+234٠٨٠٣١١١٢٢٢٢']
unique=list(dict.fromkeys(phones))
fixtures=[dict(input=v,hard=dataclasses.asdict(ns['classify_number'](v)),soft=ns['looks_like_test_data'](v)) for v in unique]
(D/'tests/number-filter-source-fixtures.json').write_text(json.dumps(dict(cases=fixtures,scope='Actual pure source number filter and phone AST; no sends, database, provider calls or WordPress runtime.'),indent=2,ensure_ascii=False)+'\n')
print(len(fixtures),'pure source number cases')
