#!/usr/bin/env python3
"""Package a WordPress-upload layout, explicitly development-only; no execution/install claim."""
import hashlib,json,re,zipfile
from pathlib import Path
D=Path(__file__).resolve().parents[1];plugin=D/'sender-suite';folder=D/'artifacts';folder.mkdir(exist_ok=True);archive=folder/'sender-suite-development.zip'
summary=json.loads((D/'tests/checks-summary.json').read_text())
if summary.get('ok') is not True:raise RuntimeError('Passing offline checks required before packaging')
version=re.search(r'\* Version: ([^\n]+)',(plugin/'sender-suite.php').read_text())[1].strip()
files=[]
for p in sorted(plugin.rglob('*')):
 if not p.is_file():continue
 rel=p.relative_to(plugin)
 if p.is_symlink() or any(part in ('node_modules','.cache','.git','__pycache__') for part in rel.parts) or p.name.startswith('.env') or p.suffix in ('.zip','.db','.sqlite','.sqlite3','.log','.pyc'):raise RuntimeError('Unexpected runtime package member: '+str(rel))
 files.append((p,'sender-suite/'+str(rel)))
files.extend(((D/'README.md','sender-suite/DEVELOPMENT-STATUS.md'),(D/'tests/checks-summary.json','sender-suite/build-evidence/offline-checks.json'),(D/'docs/contracts.json','sender-suite/build-evidence/contracts.json')))
checklist=(D/'docs/COMPLETION_CHECKLIST.md').read_text()
if version not in checklist or f"{summary['php_assertions']:,}" not in checklist:raise RuntimeError('Regenerate the completion checklist before packaging')
for p in sorted((D/'docs').glob('*')):
 if p.is_file() and not p.is_symlink() and p.suffix in ('.md','.json'):files.append((p,'sender-suite/docs/'+p.name))
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for p,name in files:
  info=zipfile.ZipInfo(name,date_time=(2026,11,7,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,p.read_bytes(),compresslevel=9)
with zipfile.ZipFile(archive) as z:
 if z.testzip() is not None:raise RuntimeError('Plugin archive CRC failure')
 names=z.namelist();assert 'sender-suite/sender-suite.php' in names and all(n.startswith('sender-suite/') and '..' not in Path(n).parts for n in names)
record={'file':archive.name,'version':version,'files':len(files),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'wordpress_upload_layout_verified':True,'archive_crc_verified':True,'complete_application':False,'native_install_verified':False,'production_ready':False,'offline_php_assertions':summary['php_assertions'],'note':'Layout and CRC checks are not a WordPress activation test. Business messaging workflows remain missing. See embedded DEVELOPMENT-STATUS.md.'}
(folder/'plugin-info.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
