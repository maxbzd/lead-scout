// Офлайн-оболочка: страницы, стили и скрипты открываются без сети. Данные (API) всегда берутся с сервера.
const VERSION = 'scout-v4'; // при изменении приложения меняем номер: старый кэш сбрасывается
const SHELL = [
  '/', '/app.css', '/fonts/fonts.css', '/manifest.webmanifest', '/icons/favicon.svg',
  '/js/main.js', '/js/api.js', '/js/state.js', '/js/ui.js',
  '/js/views/feed.js', '/js/views/detail.js', '/js/views/connect.js', '/js/views/chats.js', '/js/views/stats.js', '/js/views/profile.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  const immutable = /\.(woff2|png)$/.test(url.pathname) && (url.pathname.startsWith('/fonts/') || url.pathname.startsWith('/icons/'));
  e.respondWith(immutable ? cacheFirst(req) : networkFirst(req));
});

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) (await caches.open(VERSION)).put(req, res.clone());
  return res;
}

async function networkFirst(req) {
  try {
    const res = await fetch(req, { signal: AbortSignal.timeout(4000) }); // плохая сеть: через 4 секунды берём сохранённое
    if (res.ok) (await caches.open(VERSION)).put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) return hit;
    if (req.mode === 'navigate') {
      const shell = await caches.match('/');
      if (shell) return shell;
    }
    throw err;
  }
}
