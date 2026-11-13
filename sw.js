const CACHE_NAME = 'newsprism-v1';
const STATIC_ASSETS = [
  './manifest.json',
  './icon.png',
  './icon-192.png',
  './icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 1. 静的アセット（manifest.json と icon*.png のみ）は Cache First (Stale-While-Revalidate)
  const isStaticAsset =
    url.pathname.endsWith('/manifest.json') ||
    url.pathname.endsWith('manifest.json') ||
    /\/icon[^/]*\.png$/i.test(url.pathname);

  if (isStaticAsset) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(req, { ignoreSearch: true }).then((cached) => {
          const networkFetch = fetch(req).then((res) => {
            if (res && res.ok) {
              cache.put(req, res.clone());
            }
            return res;
          }).catch(() => cached);
          return cached || networkFetch;
        })
      )
    );
    return;
  }

  // 2. HTMLナビゲーションおよび全データ系ファイル (news_data.js?v=..., data/news/*.js, *.json 等) は厳格な Network First
  event.respondWith(
    fetch(req, { cache: 'no-cache' })
      .then((res) => {
        if (res && res.ok) {
          const resClone = res.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(req, resClone);
          });
        }
        return res;
      })
      .catch(() =>
        caches.match(req, { ignoreSearch: true }).then((cached) => {
          if (cached) return cached;
          if (req.mode === 'navigate') {
            return caches.match('./index.html', { ignoreSearch: true });
          }
          return new Response('', { status: 504, statusText: 'Offline' });
        })
      )
  );
});
