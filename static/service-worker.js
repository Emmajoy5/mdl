// Caches only static assets (icons, logo) -- never catalog pages or data, so
// installed devices always see the lab's current, live records rather than a
// stale snapshot from the moment the app was installed.
const CACHE_NAME = "mdl-static-v1";
const STATIC_ASSETS = [
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-512.png",
  "/static/logo-sm.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (STATIC_ASSETS.some((p) => url.pathname === p)) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
  // everything else (catalog pages, uploads, models, API-ish routes) always
  // goes to the network -- never served stale.
});
