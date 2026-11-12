#!/usr/bin/env python3
"""Retain available installed production-dependency license notices for the unchanged frontend build."""
import json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'wordpress/sender-suite/licenses/frontend';OUT.mkdir(parents=True,exist_ok=True)
lock=json.loads((ROOT/'package-lock.json').read_text());records=[]
for package,spec in lock['packages'].items():
 if not package or spec.get('dev'):continue
 source=ROOT/package
 if not source.is_dir():continue
 meta=source/'package.json';data=json.loads(meta.read_text()) if meta.is_file() else {};name=data.get('name',package);dest=OUT/package.replace('node_modules/','').replace('/','_');dest.mkdir(exist_ok=True)
 files=[]
 for file in source.iterdir():
  if file.is_file() and any(file.name.upper().startswith(prefix) for prefix in ('LICENSE','LICENCE','COPYING','NOTICE','COPYRIGHT')):shutil.copyfile(file,dest/file.name);files.append(file.name)
 records.append({'name':name,'version':data.get('version',spec.get('version')),'license':data.get('license',spec.get('license')),'files':files,'notice_review_required':not bool(files)})
(OUT.parent/'frontend-dependencies.json').write_text(json.dumps(records,indent=2)+'\n')
print({'dependencies':len(records),'notice_review_required':sum(r['notice_review_required'] for r in records)})
