#!/usr/bin/env python3
"""Create an explicitly INCOMPLETE reproducible recovery archive, never caches/envs/databases."""
import hashlib,json,zipfile
from pathlib import Path
D=Path(__file__).resolve().parents[1];folder=D/'artifacts';folder.mkdir(exist_ok=True);archive=folder/'sender-suite-recreated-INCOMPLETE.zip';files=[]
for p in sorted(D.rglob('*')):
 if not p.is_file():continue
 rel=p.relative_to(D)
 if any(part in ('.cache','node_modules','artifacts','__pycache__') for part in rel.parts):continue
 if p.name.startswith('.env') or p.suffix in ('.zip','.db','.sqlite','.sqlite3','.log','.pyc'):continue
 files.append((p,rel))
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for p,rel in files:z.write(p,'wordpress/'+str(rel))
with zipfile.ZipFile(archive) as z:
 if z.testzip() is not None:raise RuntimeError('Recovery archive CRC failure')
record={'file':archive.name,'files':len(files),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'complete_application':False,'native_install_verified':False,'includes':'Repository-backed recreated plugin, generated metadata, unchanged compiled UI, vendor/licenses, tools, fixtures and fresh evidence','excludes':'Original backend/frontend source (already in parent repo), caches, build-only node_modules, envs, databases, logs, prior archives'}
(folder/'recovery-info.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
