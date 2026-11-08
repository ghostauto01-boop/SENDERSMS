#!/usr/bin/env python3
"""Fetch pinned public runtime dependencies, retain licenses, scope namespaces to avoid WP conflicts."""
import hashlib,io,json,urllib.request,zipfile
from pathlib import Path
D=Path(__file__).resolve().parents[1];V=D/'sender-suite/vendor';V.mkdir(parents=True,exist_ok=True);cache=D/'.cache';cache.mkdir(exist_ok=True)
packages=[('phone','giggsey/libphonenumber-for-php','8.13.52',{'libphonenumber\\':'SenderSuite\\Vendor\\Phone\\','giggsey\\Locale\\':'SenderSuite\\Vendor\\Locale\\'}),('locale','giggsey/Locale','2.7.0',{'giggsey\\Locale\\':'SenderSuite\\Vendor\\Locale\\'}),('mbstring','symfony/polyfill-mbstring','v1.31.0',{'Symfony\\Polyfill\\Mbstring':'SenderSuite\\Vendor\\Mbstring'})]
records=[]
for folder,repo,version,replacements in packages:
 url=f'https://codeload.github.com/{repo}/zip/refs/tags/{version}';archive=cache/(folder+'.zip')
 if not archive.exists():archive.write_bytes(urllib.request.urlopen(url,timeout=60).read())
 raw=archive.read_bytes();z=zipfile.ZipFile(io.BytesIO(raw));files={}
 for member in z.infolist():
  if member.is_dir():continue
  rel=Path(*Path(member.filename).parts[1:]);parts=rel.parts
  if not parts or not (parts[0] in ('src','data','LICENSE','LICENSE.txt','README.md','bootstrap.php','bootstrap80.php','Mbstring.php','composer.json')):continue
  target=V/folder/rel;target.parent.mkdir(parents=True,exist_ok=True);data=z.read(member)
  if target.suffix=='.php':
   text=data.decode()
   text=text.replace('namespace libphonenumber;', 'namespace SenderSuite\\Vendor\\Phone;').replace('Giggsey\\Locale','SenderSuite\\Vendor\\Locale')
   for source,dest in replacements.items():text=text.replace(source,dest)
   data=text.encode()
  target.write_bytes(data);files[str(rel)]=hashlib.sha256(data).hexdigest()
 records.append({'package':repo,'version':version,'url':url,'archive_sha256':hashlib.sha256(raw).hexdigest(),'files':files,'modifications':'PHP namespaces/references scoped to SenderSuite Vendor; metadata and licenses retained'})
(D/'sender-suite/schema/phone-vendor-manifest.json').write_text(json.dumps(records,indent=2)+'\n');print({r['package']:len(r['files']) for r in records})
