#!/usr/bin/env python3
"""Fail closed on stale copied frontend assets/build inputs. Does not certify any backend workflow."""
import hashlib,json
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;APP=D/'sender-suite/app'
def verify():
 manifest=json.loads((D/'tests/native-ui-build.json').read_text());checks=0
 for group,base in [('build_input_hashes',ROOT),('overlay_hashes',D),('asset_hashes',APP)]:
  for name,digest in manifest[group].items():
   p=base/name
   if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('Stale native UI '+group+': '+name)
   checks+=1
 actual={str(p.relative_to(APP)) for p in APP.rglob('*') if p.is_file()}
 if actual!=set(manifest['asset_hashes']):raise RuntimeError('Unexpected or missing native UI assets')
 if 'sw.js' in actual or 'manifest.json' in actual:raise RuntimeError('Root-scoped original PWA artifacts must not be deployed')
 html=(APP/'index.html').read_text()
 if 'sender-suite-native-ui' not in html or 'serviceWorker.register' in html:raise RuntimeError('Invalid native shell build')
 return {'ok':True,'checks':checks+3,'scope':'SHA256 build-input/overlay/generated-asset provenance and no-original-service-worker checks only; not runtime or native workflow certification.'}
if __name__=='__main__':print(json.dumps(verify()))
