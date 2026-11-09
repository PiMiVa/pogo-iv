// Pogo IV als installierbare App (PWA): immer zuerst das Netz, damit Updates sofort ankommen.
// Ohne Netz kommt die zuletzt geladene Fassung aus dem Speicher (Sammlung liegt ohnehin im Browser).
const CACHE = "pogo-iv";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || !req.url.startsWith("http")) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        // Nur die App selbst und die Texterkennung speichern, nicht jedes Bildchen.
        const keep = new URL(req.url).origin === self.location.origin || /jsdelivr|projectnaptha/.test(req.url);
        if (keep && (res.ok || res.type === "opaque")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req, { ignoreSearch: false }).then((r) => r || caches.match(req, { ignoreSearch: true })))
  );
});
