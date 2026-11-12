/** Same-origin WordPress transport and hash navigation. Never falls back to the original service. */
export interface NativeRuntime { apiRoot: string; assetBase: string; smsOutboxEnabled: boolean; productionReady: false; }
declare global { interface Window { SENDER_SUITE?: NativeRuntime; } }
export function nativeRuntime(): NativeRuntime {
  const runtime=window.SENDER_SUITE;
  if(!runtime || runtime.productionReady!==false) throw new Error('Native WordPress runtime is missing. Open the application from Settings → Sender Suite.');
  for(const raw of [runtime.apiRoot,runtime.assetBase]) {
    const url=new URL(raw,window.location.href);
    if(!['http:','https:'].includes(url.protocol)||url.origin!==window.location.origin||url.username||url.password||url.hash) throw new Error('WordPress application and API must use the same origin.');
  }
  return runtime;
}
export function nativeApiUrl(path:string): string {
  if(!/^\/(?!\/)/.test(path)||path.includes('\\')||/[\u0000-\u0020]/.test(path)||path.includes('#')) throw new Error('Only native relative API paths are allowed.');
  const [pathname,query='']=path.split(/\?(.*)/s);
  for(const segment of pathname.split('/')) {const decoded=decodeURIComponent(segment);if(decoded==='.'||decoded==='..'||/[\\/\u0000-\u0020]/.test(decoded))throw new Error('Invalid API path segment.');}
  const url=new URL(nativeRuntime().apiRoot,window.location.href);
  if(url.searchParams.has('rest_route')) url.searchParams.set('rest_route',url.searchParams.get('rest_route')!.replace(/\/$/,'')+pathname);
  else url.pathname=url.pathname.replace(/\/$/,'')+pathname;
  for(const [name,value] of new URLSearchParams(query)){
    if(['rest_route','sender_suite_app','_method','_jsonp','_envelope'].includes(name))throw new Error('Reserved native API query parameter.');
    url.searchParams.append(name,value);
  }
  return url.href;
}
export function nativeAssetUrl(path:string): string {
  if(!/^[A-Za-z0-9_.\/-]+$/.test(path)||path.startsWith('/')||path.split('/').some(x=>x==='..'||x==='.'))throw new Error('Invalid application asset path.');
  return new URL(path,nativeRuntime().assetBase).href;
}
export function routeSearch(): string {const hash=window.location.hash;const at=hash.indexOf('?');return at<0?'':hash.slice(at);}
export function navigateNative(path:string){if(!/^\/(?!\/)/.test(path)||/[\r\n\\#]/.test(path))throw new Error('Invalid application route.');window.location.hash=path;}
export function loginNative(){if(!window.location.hash.startsWith('#/login'))navigateNative('/login');}
