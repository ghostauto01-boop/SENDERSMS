#!/usr/bin/env python3
"""Build a copied source UI plus reviewed native overlays; never modifies original frontend/backend."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
D=Path(__file__).resolve().parents[1];ROOT=D.parent;WORK=D/'.cache/ui';OVERLAY=D/'ui/overlay';APP=D/'sender-suite/app'
source=json.loads((D/'sender-suite/schema/frontend-hashes.json').read_text())
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in source.items()),'Original frontend changed'
if WORK.exists():shutil.rmtree(WORK)
shutil.copytree(ROOT/'frontend/src',WORK/'src',ignore=shutil.ignore_patterns('*.test.ts','*.test.tsx','test'))
shutil.copytree(ROOT/'frontend/public',WORK/'public',ignore=shutil.ignore_patterns('sw.js','manifest.json'))
(WORK/'node_modules').symlink_to(ROOT/'node_modules',target_is_directory=True)
index=(ROOT/'frontend/index.html').read_text();index=re.sub(r'<script>\s*if \(\x27serviceWorker\x27 in navigator\).*?</script>','',index,flags=re.S);index=re.sub(r'\s*<link rel="manifest"[^>]+>','',index);index=index.replace('href="/','href="./');index=index.replace('<head>','<head><meta name="sender-suite-native-ui" content="1">');index='\n'.join(line.rstrip() for line in index.splitlines())+'\n';(WORK/'index.html').write_text(index)
def edit(rel,old,new,count=1):
 p=WORK/'src'/rel;s=p.read_text();assert s.count(old)==count,(rel,old,s.count(old));p.write_text(s.replace(old,new))
def use_runtime(rel,names):
 p=WORK/'src'/rel;prefix='../native/runtime' if '/' in rel else './native/runtime';p.write_text('import { '+names+' } from "'+prefix+'";\n'+p.read_text())
for src,dest in [('runtime.ts','native/runtime.ts'),('main.tsx','main.tsx'),('client.ts','api/client.ts'),('useAuth.tsx','hooks/useAuth.tsx'),('NativeSendPage.tsx','pages/SendPage.tsx')]:
 p=WORK/'src'/dest;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(OVERLAY/src,p)
for rel in ['pages/AIAgentPage.tsx','pages/ContactsPage.tsx','pages/EmailManagerPage.tsx','pages/SMSManagerPage.tsx','pages/SettingsPage.tsx']:
 p=WORK/'src'/rel;s=p.read_text();assert 'window.location.search' in s;use_runtime(rel,'routeSearch');p.write_text(p.read_text().replace('window.location.search','routeSearch()'))
use_runtime('components/agent/AgentQuickAsk.tsx','navigateNative');p=WORK/'src/components/agent/AgentQuickAsk.tsx';p.write_text(p.read_text().replace('"../native/runtime"','"../../native/runtime"').replace('window.location.assign(','navigateNative('))
for rel in ['pages/EmailManagerPage.tsx','pages/SMSManagerPage.tsx']:edit(rel,'href="/audiences"','href="#/audiences"',count=2)
edit('pages/PhonePage.tsx','href="/settings"','href="#/settings"')
edit('pages/ListsPage.tsx','href={`/validator?list_id=','href={`#/validator?list_id=')
use_runtime('api/ads.ts','nativeApiUrl');edit('api/ads.ts','return `/api/v1/ads/export/${kind}${query ? `?${query}` : ""}`;','return nativeApiUrl(`/ads/export/${kind}${query ? `?${query}` : ""}`);')
use_runtime('api/aiAgent.ts','nativeApiUrl');edit('api/aiAgent.ts','fetch(`/api/v1/ai-agent/sessions/${sessionId}/chat`,','fetch(nativeApiUrl(`/ai-agent/sessions/${sessionId}/chat`),')
use_runtime('api/mcp.ts','nativeApiUrl');edit('api/mcp.ts','endpoint: () => `${window.location.origin}/mcp`,','endpoint: () => nativeApiUrl("/mcp-wire/mcp"),')
edit('utils/push.ts','return (\n    typeof window', 'return (\n    false && typeof window')
# Keep inbox viewing/controls but direct reply operators to the safe explicit-sender native composer.
use_runtime('pages/InboxPage.tsx','navigateNative');p=WORK/'src/pages/InboxPage.tsx';s=p.read_text();a=s.index('  const sendReply = async () => {');b=s.index('\n  };',a)+len('\n  };');s=s[:a]+'''  const sendReply = async () => {
    if (!selected) return;
    navigateNative(`/send?conversation_id=${selected.id}&contact_id=${selected.contact_id}`);
    toast('Select an explicit sender and confirm your final text in the native queue composer.');
  };'''+s[b:];p.write_text(s)
# Disable original host keepalive and stale free-instance banners; native failures remain explicit in page errors.
p=WORK/'src/hooks/useServerKeepAlive.ts';p.write_text('export default function useServerKeepAlive() {}\n')
config=json.loads((ROOT/'tsconfig.json').read_text());config['include']=['src'];config['compilerOptions']['baseUrl']='.';config['compilerOptions']['paths']={'@/*':['src/*']};(WORK/'tsconfig.json').write_text(json.dumps(config))
subprocess.run([str(ROOT/'node_modules/.bin/tsc'),'-p',str(WORK/'tsconfig.json')],cwd=ROOT,check=True)
subprocess.run([str(ROOT/'node_modules/.bin/vite'),'build','--config',str(D/'ui/vite.config.mjs')],cwd=ROOT,check=True)
inputs=[Path(__file__).resolve(),D/'ui/vite.config.mjs',ROOT/'package.json',ROOT/'package-lock.json',ROOT/'tailwind.config.js',ROOT/'tsconfig.json']
manifest={'build_input_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'original_frontend_unchanged':True,'original_files':len(source),'overlay_hashes':{str(p.relative_to(D)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OVERLAY.glob('*')) if p.is_file()},'asset_hashes':{str(p.relative_to(APP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(APP.rglob('*')) if p.is_file()},'full_workflow_parity':False,'native_wordpress_browser_verified':False,'note':'Copied original interface with same-origin WordPress transport, hash routing and guarded queue composer. Unsupported backend workflows remain unavailable; not release certification.'}
(D/'tests/native-ui-build.json').write_text(json.dumps(manifest,indent=2)+'\n')
