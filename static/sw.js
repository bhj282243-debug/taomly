// sw.js — Taomly Service Worker
// Стратегия:
//   - Статика (CSS, JS, шрифты): Cache First → быстро, офлайн работает
//   - API-запросы: Network First → актуальные данные, фолбэк на кэш
//   - Офлайн-страница: если нет сети и нет кэша → показываем offline.html

const CACHE_NAME = 'taomly-v1';
const OFFLINE_URL = '/static/offline.html';

// Ресурсы для предварительного кэширования при установке SW
const PRECACHE_URLS = [
  '/app',
  '/static/manifest.json',
  OFFLINE_URL,
];

// ──────────────────────────────────────────
// INSTALL — предварительное кэширование
// ──────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_URLS);
    }).then(() => {
      // Активируем SW сразу, не ждём закрытия вкладок
      return self.skipWaiting();
    })
  );
});

// ──────────────────────────────────────────
// ACTIVATE — очистка старых кэшей
// ──────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    }).then(() => {
      // Берём управление всеми клиентами без перезагрузки
      return self.clients.claim();
    })
  );
});

// ──────────────────────────────────────────
// FETCH — стратегии кэширования
// ──────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Не кэшируем POST, PATCH, DELETE — только GET
  if (request.method !== 'GET') return;

  // Не кэшируем webhook и Telegram API
  if (url.pathname.startsWith('/webhook') || url.hostname === 'api.telegram.org') return;

  // API-запросы: Network First
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Статические ресурсы и страницы: Cache First
  event.respondWith(cacheFirst(request));
});

// ──────────────────────────────────────────
// СТРАТЕГИЯ: Cache First
// Для статики — шрифты, иконки, manifest
// ──────────────────────────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    // Кэшируем только успешные ответы
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Офлайн и нет кэша — показываем offline страницу
    const offline = await caches.match(OFFLINE_URL);
    return offline || new Response('Нет соединения', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
}

// ──────────────────────────────────────────
// СТРАТЕГИЯ: Network First
// Для API — всегда свежие данные, фолбэк на кэш
// ──────────────────────────────────────────
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(JSON.stringify({ error: 'Нет соединения' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}
