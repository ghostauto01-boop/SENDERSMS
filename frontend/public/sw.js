const V = "sms-sender-v3";
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

// --- Inbuilt browser push (VAPID, free forever) ---
self.addEventListener("push", e => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch {
    d = { title: "SMS SENDER", body: e.data ? e.data.text() : "" };
  }
  const title = d.title || "SMS SENDER";
  e.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || "",
      icon: d.icon || "/icon-192.png",
      badge: d.badge || "/icon-192.png",
      tag: d.tag || "sendsms",
      renotify: true,
      data: { url: d.url || "/" },
      vibrate: [100, 50, 100],
    })
  );
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  const target = new URL(url, self.location.origin).href;
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url === target && "focus" in c) return c.focus();
      }
      for (const c of list) {
        if (new URL(c.url).origin === self.location.origin && "navigate" in c) {
          return c.navigate(target).then(cc => cc.focus());
        }
      }
      if (clients.openWindow) return clients.openWindow(target);
    })
  );
});
