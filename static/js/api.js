// ── API.JS — API communication layer (R-3.2) ─────────────────────────────────
// Все fetch-вызовы находятся в соответствующих domain-модулях:
//   menu.js    → GET /api/restaurants/{slug}, GET /api/restaurants/{slug}/table/{n}
//   orders.js  → POST /api/orders/, GET /api/orders/my/{id}
//   i18n.js    → GET /i18n/{lang}.json
//
// Этот файл зарезервирован для будущего выделения API-слоя.
// Не добавлять логику сюда без отдельного архитектурного решения.
//
// API endpoints (зафиксированы, не менять):
//   GET  /api/restaurants/{slug}
//   GET  /api/restaurants/{slug}/table/{n}
//   POST /api/orders/
//   GET  /api/orders/my/{id}
//   GET  /i18n/{lang}.json
