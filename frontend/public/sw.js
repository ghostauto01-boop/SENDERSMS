const V = "sms-sender-v2";
const APP_SHELL = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(V).then(c => c.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== V).map(k => caches.delete(k)))
    )
  );
  clients.claim();
});

self.addEventListener("fetch", e => {
  // Don't cache API calls
  if (e.request.url.includes("/api/")) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || fetch(e.request).then(resp => {
        if (resp.ok && e.request.method === "GET") {
          const clone = resp.clone();
          caches.open(V).then(c => c.put(e.request, clone));
        }
        return resp;
      })
    )
  );
});
