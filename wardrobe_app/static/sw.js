self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Minimal SW for "installable" PWA; no aggressive caching by default.
self.addEventListener('fetch', () => {});
