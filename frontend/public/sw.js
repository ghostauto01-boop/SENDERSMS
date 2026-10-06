/**
 * Service worker: offline shell + background push alerts.
 *
 * The caching rules matter more than they look. The app shell (HTML) is served
 * **network first**, because a cache-first HTML document pins whatever build
 * the device saw first: ship a new UI and a phone that has the app installed
 * keeps showing the old one, which is indistinguishable from "the update did
 * nothing". Hashed build output under /assets/ is the opposite — the filename
 * changes with the content, so it is safe (and fast) to serve cache first.
 *
 * Bump VERSION on any change here: it evicts every old cache on activate.
 */
const VERSION = "sms-sender-v7";
const ASSET_CACHE = `${VERSION}-assets`;
const SHELL_CACHE = `${VERSION}-shell`;

const APP_SHELL = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const request = e.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Never touch the API, the MCP surface or the OAuth documents: a stale
  // answer here would look like a broken connector rather than a cached one.
  if (/^\/(api|mcp|connectors)\//.test(url.pathname) || url.pathname.startsWith("/.well-known/")) {
    return;
  }

  // Build output is content-hashed: cache first, forever.
  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((resp) => {
            if (resp.ok) {
              const clone = resp.clone();
              caches.open(ASSET_CACHE).then((c) => c.put(request, clone));
            }
            return resp;
          })
      )
    );
    return;
  }

  // Everything else (the HTML document above all): network first, cache only
  // as an offline fallback.
  e.respondWith(
    fetch(request)
      .then((resp) => {
        if (resp.ok && (request.mode === "navigate" || request.destination === "document")) {
          const clone = resp.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(request, clone));
        }
        return resp;
      })
      .catch(() =>
        caches.match(request).then((hit) => hit || caches.match("/"))
      )
  );
});

// --- Inbuilt browser push (VAPID, free forever) ---
self.addEventListener("push", (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch {
    d = { title: "SMS SENDER", body: e.data ? e.data.text() : "" };
  }
  // title carries the kind and the sender ("📧 New email from Ada Obi"), body
  // carries the subject and the first line of the message — so a reply is
  // readable from a locked phone screen without opening the app.
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

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  const target = new URL(url, self.location.origin).href;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url === target && "focus" in c) return c.focus();
      }
      for (const c of list) {
        if (new URL(c.url).origin === self.location.origin && "navigate" in c) {
          return c.navigate(target).then((cc) => cc.focus());
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
