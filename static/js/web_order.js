/**
 * web_order.js — Phase 13: Public Web Ordering Bootstrap
 *
 * Entry point for /r/{slug} pages.
 * Reads slug from window.__TAOMLY_WEB__.slug (injected by SSR template).
 * Uses existing Cart Engine (X-Restaurant-Id, X-Location-Id, X-Cart-Session).
 *
 * SECURITY:
 *   - Cart controls disabled until restaurant/location data loaded (prevents
 *     empty X-Location-Id being sent — fixes GAP-H3).
 *   - Client never submits total_amount, currency, restaurant_id, location_id.
 *   - Idempotency key generated when checkout form displayed (not on submit).
 *   - Raw web_order_token stored in localStorage per order_id after checkout.
 *
 * Architecture:
 *   SSR HTML → JS loads → fetchRestaurant() → renderMenu() → Cart → Checkout
 *
 * DO NOT import this in index.html (Telegram/QR SPA).
 * DO NOT modify app.css, cart.js, orders.js, state.js.
 */

(function () {
  'use strict';

  /* ── CONFIG ─────────────────────────────────────────────────── */
  const API_BASE = '';
  const CART_SESSION_KEY = 'cart_session_id';

  /* ── STATE ─────────────────────────────────────────────────── */
  let _restaurant = null;
  let _location_id = null;
  let _restaurant_id = null;
  let _cartItems = [];          // { product_id, variant_id, name, price, quantity, modifiers[] }
  let _checkoutIdempotencyKey = null;
  let _view = 'menu';           // 'menu' | 'checkout' | 'confirmation'
  let _confirmationOrder = null;

  /* ── SESSION ────────────────────────────────────────────────── */
  function getOrCreateSessionId() {
    let sid = localStorage.getItem(CART_SESSION_KEY);
    if (!sid) {
      sid = crypto.randomUUID();
      localStorage.setItem(CART_SESSION_KEY, sid);
    }
    return sid;
  }

  /* ── CART HEADERS (mirrored from state.js pattern) ──────────── */
  // Controls are disabled until _restaurant_id and _location_id are set.
  function cartHeaders() {
    return {
      'Content-Type': 'application/json',
      'X-Restaurant-Id': String(_restaurant_id || ''),
      'X-Location-Id': String(_location_id || ''),
      'X-Cart-Session': getOrCreateSessionId(),
    };
  }

  function cartReady() {
    return _restaurant_id !== null && _location_id !== null;
  }

  /* ── API HELPERS ────────────────────────────────────────────── */
  async function apiFetch(path, opts = {}) {
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw Object.assign(new Error(body.detail || res.statusText), { status: res.status });
    }
    return res.json();
  }

  /* ── PRICE FORMAT ────────────────────────────────────────────── */
  function fmtPrice(amount, currency) {
    const symbols = { UZS: "so'm", KZT: '₸', RUB: '₽', USD: '$', TRY: '₺', AED: 'AED' };
    const sym = symbols[currency] || currency || '';
    const formatted = Math.round(amount).toLocaleString('ru-RU').replace(/,/g, ' ');
    return `${formatted} ${sym}`.trim();
  }

  /* ── RESTAURANT / MENU LOAD ─────────────────────────────────── */
  async function fetchRestaurant(slug) {
    return apiFetch(`/api/restaurants/${encodeURIComponent(slug)}`);
  }

  function applyBranding(r) {
    const root = document.documentElement;
    if (r.primary_color)   root.style.setProperty('--color-primary',   r.primary_color);
    if (r.secondary_color) root.style.setProperty('--color-secondary', r.secondary_color);
    if (r.accent_color)    root.style.setProperty('--color-accent',    r.accent_color);
  }

  /* ── CART OPERATIONS ─────────────────────────────────────────── */
  async function serverAddItem(productId, variantId, modifierOptionIds) {
    return apiFetch('/api/cart/items', {
      method: 'POST',
      headers: cartHeaders(),
      body: JSON.stringify({
        product_id: productId,
        quantity: 1,
        variant_id: variantId || null,
        modifier_option_ids: modifierOptionIds || [],
      }),
    });
  }

  async function serverGetCart() {
    if (!cartReady()) return null;
    return apiFetch('/api/cart', { headers: cartHeaders() });
  }

  async function serverUpdateItem(itemId, quantity) {
    return apiFetch(`/api/cart/items/${itemId}`, {
      method: 'PATCH',
      headers: cartHeaders(),
      body: JSON.stringify({ quantity }),
    });
  }

  async function serverRemoveItem(itemId) {
    return apiFetch(`/api/cart/items/${itemId}`, {
      method: 'DELETE',
      headers: cartHeaders(),
    });
  }

  /* ── CHECKOUT ────────────────────────────────────────────────── */
  async function doCheckout(orderType, clientName, clientPhone, address, comment) {
    if (!_checkoutIdempotencyKey) {
      _checkoutIdempotencyKey = crypto.randomUUID();
    }

    const payload = {
      order_type: orderType,
      client_name: clientName || null,
      client_phone: clientPhone || null,
      address: orderType === 'delivery' ? (address || null) : null,
      comment: comment || null,
      idempotency_key: _checkoutIdempotencyKey,
      // NEVER include: total_amount, currency, restaurant_id, location_id
    };

    return apiFetch('/api/cart/checkout', {
      method: 'POST',
      headers: cartHeaders(),
      body: JSON.stringify(payload),
    });
  }

  /* ── TOKEN STORAGE ───────────────────────────────────────────── */
  function storeWebToken(orderId, rawToken) {
    if (rawToken) {
      try {
        localStorage.setItem(`web_order_token_${orderId}`, rawToken);
      } catch (_) { /* storage full — not fatal */ }
    }
  }

  function getStoredToken(orderId) {
    try {
      return localStorage.getItem(`web_order_token_${orderId}`);
    } catch (_) { return null; }
  }

  /* ── RENDER ──────────────────────────────────────────────────── */
  function renderApp() {
    const app = document.getElementById('web-app');
    if (!app) return;

    if (_view === 'menu') {
      renderMenu(app);
    } else if (_view === 'checkout') {
      renderCheckout(app);
    } else if (_view === 'confirmation') {
      renderConfirmation(app);
    }
  }

  function renderMenu(app) {
    const r = _restaurant;
    if (!r) {
      app.innerHTML = '<div class="web-loading">Загрузка меню...</div>';
      return;
    }

    const categories = r.categories || [];
    const currency = r.currency || 'UZS';

    let html = `<div class="web-page">`;

    // Header
    html += `<header class="web-header"><div class="web-header__inner">`;
    if (r.logo_url) {
      html += `<img class="web-header__logo" src="${esc(r.logo_url)}" alt="${esc(r.name)}">`;
    }
    html += `<span class="web-header__name">${esc(r.name)}</span>`;
    html += `</div></header>`;

    // Restaurant info
    html += `<div class="web-restaurant-info">`;
    if (r.welcome_text) {
      html += `<p class="web-restaurant-info__meta">${esc(r.welcome_text)}</p>`;
    }
    if (r.working_hours) {
      html += `<p class="web-restaurant-info__meta">⏰ ${esc(r.working_hours)}</p>`;
    }
    html += `</div>`;

    // Menu categories
    html += `<div class="web-menu">`;
    if (categories.length === 0) {
      html += `<div class="web-loading">Меню временно недоступно.</div>`;
    } else {
      categories.forEach(cat => {
        html += `<div class="web-category">
          <div class="web-category__title">${esc(cat.name)}</div>
          <div class="web-product-grid">`;
        cat.products.forEach(p => {
          const price = p.price !== null ? fmtPrice(p.price, currency) : '';
          const img = p.photo_url
            ? `<img class="web-product-card__image" src="${esc(p.photo_url)}" alt="${esc(p.name)}" loading="lazy">`
            : `<div class="web-product-card__image"></div>`;
          html += `<div class="web-product-card" data-product-id="${p.id}" onclick="WEB.addToCart(${p.id})">
            ${img}
            <div class="web-product-card__body">
              <div class="web-product-card__name">${esc(p.name)}</div>
              ${p.description ? `<div class="web-product-card__desc">${esc(p.description)}</div>` : ''}
              <div class="web-product-card__footer">
                <span class="web-product-card__price">${price}</span>
                <button class="web-btn web-btn--primary" style="padding:6px 14px;font-size:.8rem;"
                  onclick="event.stopPropagation();WEB.addToCart(${p.id})">+</button>
              </div>
            </div>
          </div>`;
        });
        html += `</div></div>`;
      });
    }
    html += `</div>`; // web-menu

    // Cart bar (hidden until items exist)
    html += renderCartBar(currency);

    html += `</div>`; // web-page
    app.innerHTML = html;
    updateCartBar();
  }

  function renderCartBar(currency) {
    const count = _cartItems.reduce((s, i) => s + i.quantity, 0);
    const total = _cartItems.reduce((s, i) => s + i.unit_price * i.quantity, 0);
    const cur = currency || (_restaurant && _restaurant.currency) || 'UZS';
    const hidden = count === 0 ? 'web-cart-bar--hidden' : '';
    return `<div class="web-cart-bar ${hidden}" id="web-cart-bar">
      <div class="web-cart-bar__info">
        <span class="web-cart-bar__count">${count} товар(ов)</span>
      </div>
      <span class="web-cart-bar__total">${fmtPrice(total, cur)}</span>
      <button class="web-btn web-btn--secondary" onclick="WEB.goCheckout()">
        Оформить заказ →
      </button>
    </div>`;
  }

  function updateCartBar() {
    const bar = document.getElementById('web-cart-bar');
    if (!bar || !_restaurant) return;
    const count = _cartItems.reduce((s, i) => s + i.quantity, 0);
    const total = _cartItems.reduce((s, i) => s + i.unit_price * i.quantity, 0);
    const cur = _restaurant.currency || 'UZS';
    bar.querySelector('.web-cart-bar__count').textContent = `${count} товар(ов)`;
    bar.querySelector('.web-cart-bar__total').textContent = fmtPrice(total, cur);
    bar.classList.toggle('web-cart-bar--hidden', count === 0);
  }

  function renderCheckout(app) {
    const cur = (_restaurant && _restaurant.currency) || 'UZS';
    const total = _cartItems.reduce((s, i) => s + i.unit_price * i.quantity, 0);
    const minOrder = (_restaurant && _restaurant.min_order_amount) || 0;

    // Generate idempotency key when checkout form first displayed
    if (!_checkoutIdempotencyKey) {
      _checkoutIdempotencyKey = crypto.randomUUID();
    }

    app.innerHTML = `<div class="web-page">
      <header class="web-header">
        <div class="web-header__inner">
          <button class="web-btn web-btn--secondary" style="padding:6px 14px"
            onclick="WEB.goMenu()">← Меню</button>
          <span class="web-header__name">Оформление заказа</span>
        </div>
      </header>
      <div class="web-checkout">
        <div id="web-checkout-error" class="web-error" style="display:none"></div>

        <div class="web-form-group">
          <label>Тип заказа</label>
          <select id="wc-order-type" onchange="WEB.onOrderTypeChange()">
            <option value="takeaway">Самовывоз</option>
            <option value="delivery">Доставка</option>
          </select>
        </div>

        <div class="web-form-group">
          <label>Ваше имя *</label>
          <input type="text" id="wc-name" placeholder="Имя" maxlength="100">
        </div>

        <div class="web-form-group">
          <label>Телефон *</label>
          <input type="tel" id="wc-phone" placeholder="+998 __ ___ __ __">
        </div>

        <div class="web-form-group" id="wc-address-group" style="display:none">
          <label>Адрес доставки *</label>
          <input type="text" id="wc-address" placeholder="Улица, дом, квартира" maxlength="300">
        </div>

        <div class="web-form-group">
          <label>Комментарий</label>
          <textarea id="wc-comment" rows="2" maxlength="500" placeholder="Например: позвонить за 10 минут"></textarea>
        </div>

        <div class="web-confirmation__details" style="margin-bottom:16px">
          ${_cartItems.map(i =>
            `<div class="web-confirmation__row">
              <span>${esc(i.name)}${i.variant_name ? ' / ' + esc(i.variant_name) : ''} × ${i.quantity}</span>
              <span>${fmtPrice(i.unit_price * i.quantity, cur)}</span>
            </div>`
          ).join('')}
          <div class="web-confirmation__row">
            <span>Итого</span>
            <span>${fmtPrice(total, cur)}</span>
          </div>
        </div>

        ${minOrder > 0 ? `<p style="font-size:.8rem;color:var(--color-muted);margin-bottom:12px">
          Минимальная сумма доставки: ${fmtPrice(minOrder, cur)}
        </p>` : ''}

        <button class="web-btn web-btn--primary" id="wc-submit-btn"
          style="width:100%;padding:14px"
          onclick="WEB.submitCheckout()">
          Отправить заказ
        </button>
      </div>
    </div>`;
  }

  function renderConfirmation(app) {
    const o = _confirmationOrder;
    if (!o) return;
    const cur = o.currency || 'UZS';
    app.innerHTML = `<div class="web-page">
      <div class="web-confirmation">
        <div class="web-confirmation__icon">✅</div>
        <div class="web-confirmation__title">Заказ принят!</div>
        <div class="web-confirmation__order-id">Заказ #${o.id}</div>

        <div class="web-confirmation__details">
          <div class="web-confirmation__row">
            <span>Статус</span>
            <span>${esc(o.status)}</span>
          </div>
          <div class="web-confirmation__row">
            <span>Тип</span>
            <span>${o.order_type === 'delivery' ? 'Доставка' : 'Самовывоз'}</span>
          </div>
          ${o.items.map(i =>
            `<div class="web-confirmation__row">
              <span>${esc(i.name)}${i.variant_name ? ' / ' + esc(i.variant_name) : ''} × ${i.quantity}</span>
              <span>${fmtPrice(i.price * i.quantity, cur)}</span>
            </div>`
          ).join('')}
          <div class="web-confirmation__row">
            <span>Итого</span>
            <span>${fmtPrice(o.total_amount, cur)}</span>
          </div>
          ${o.paid_at ? `<div class="web-confirmation__row"><span>Оплачено</span><span>✅</span></div>` : ''}
        </div>

        <button class="web-btn web-btn--primary" style="width:100%;padding:14px;margin-top:8px"
          onclick="WEB.goMenu()">
          Вернуться в меню
        </button>
      </div>
    </div>`;
  }

  /* ── ESCAPE HTML ─────────────────────────────────────────────── */
  function esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* ── PUBLIC API ──────────────────────────────────────────────── */
  const WEB = {

    async addToCart(productId) {
      if (!cartReady()) {
        showError('Меню ещё загружается. Попробуйте через секунду.');
        return;
      }
      try {
        await serverAddItem(productId, null, []);
        const cart = await serverGetCart();
        if (cart && cart.items) {
          _cartItems = cart.items;
        }
        updateCartBar();
      } catch (e) {
        showError(e.message || 'Не удалось добавить товар.');
      }
    },

    goCheckout() {
      if (_cartItems.length === 0) return;
      _view = 'checkout';
      renderApp();
    },

    goMenu() {
      _view = 'menu';
      renderApp();
    },

    onOrderTypeChange() {
      const t = document.getElementById('wc-order-type').value;
      const ag = document.getElementById('wc-address-group');
      if (ag) ag.style.display = t === 'delivery' ? '' : 'none';
    },

    async submitCheckout() {
      const btn = document.getElementById('wc-submit-btn');
      const errEl = document.getElementById('web-checkout-error');
      const orderType = document.getElementById('wc-order-type').value;
      const name = (document.getElementById('wc-name').value || '').trim();
      const phone = (document.getElementById('wc-phone').value || '').trim();
      const address = (document.getElementById('wc-address') || {}).value || '';
      const comment = (document.getElementById('wc-comment').value || '').trim();

      // Frontend validation (UX only — backend is authoritative)
      if (!name) { showFormError(errEl, 'Укажите ваше имя.'); return; }
      if (!phone) { showFormError(errEl, 'Укажите телефон.'); return; }
      if (orderType === 'delivery' && !address.trim()) {
        showFormError(errEl, 'Укажите адрес доставки.'); return;
      }

      btn.disabled = true;
      btn.textContent = 'Отправляем...';
      if (errEl) errEl.style.display = 'none';

      try {
        const order = await doCheckout(orderType, name, phone, address, comment);

        // Store raw token in localStorage (token returned only once)
        if (order.web_order_token) {
          storeWebToken(order.id, order.web_order_token);
        }

        // Clear local cart state (server cart is now checked_out)
        _cartItems = [];
        _checkoutIdempotencyKey = null;

        // Show confirmation
        _confirmationOrder = order;
        _view = 'confirmation';
        renderApp();

      } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Отправить заказ';
        showFormError(errEl, e.message || 'Ошибка при оформлении. Попробуйте ещё раз.');
      }
    },

  };

  function showError(msg) {
    console.error('[web_order]', msg);
  }

  function showFormError(el, msg) {
    if (!el) return;
    el.textContent = msg;
    el.style.display = '';
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /* ── INIT ────────────────────────────────────────────────────── */
  async function init() {
    // Get slug from SSR-injected window variable
    const meta = window.__TAOMLY_WEB__;
    if (!meta || !meta.slug) {
      console.error('[web_order] Missing __TAOMLY_WEB__.slug');
      return;
    }
    const slug = meta.slug;

    // Hide SSR content block (JS has taken over)
    const ssrBlock = document.getElementById('web-ssr-content');
    if (ssrBlock) ssrBlock.style.display = 'none';

    // Show loading state
    const app = document.getElementById('web-app');
    if (app) app.innerHTML = '<div class="web-loading">Загружаем меню...</div>';

    try {
      // Fetch restaurant + menu data
      _restaurant = await fetchRestaurant(slug);
      _restaurant_id = _restaurant.id;
      _location_id = _restaurant.location_id;

      // Apply branding updates from API (supplements SSR CSS vars)
      applyBranding(_restaurant);

      // Load existing cart from server (session may have items from earlier visit)
      if (cartReady()) {
        try {
          const cart = await serverGetCart();
          if (cart && cart.items) {
            _cartItems = cart.items;
          }
        } catch (_) { /* empty cart or session expired — not fatal */ }
      }

      renderApp();

    } catch (e) {
      if (app) {
        app.innerHTML = `<div class="web-error">
          Не удалось загрузить меню. Попробуйте обновить страницу.<br>
          <small>${esc(e.message || '')}</small>
        </div>`;
      }
    }
  }

  // Expose public API
  window.WEB = WEB;

  // Boot after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
