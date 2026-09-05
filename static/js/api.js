// ── API.JS — API communication layer (R-3.2) ─────────────────────────────────
// Все fetch-вызовы тесно связаны с domain-функциями (loadMenu, resolveTableId,
// submitOrder, _pollOrderStatus) и остаются в index.html до следующих R-3 этапов.
// Этот файл зарезервирован для будущего R-3.x — не добавлять сюда логику сейчас.
//
// API endpoints (зафиксированы, не менять):
//   GET  /api/restaurants/{slug}
//   GET  /api/restaurants/{slug}/table/{n}
//   POST /api/orders/
//   GET  /api/orders/my/{id}
//   GET  /i18n/{lang}.json
