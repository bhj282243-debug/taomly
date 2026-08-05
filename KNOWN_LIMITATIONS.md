# Known Limitations

This document lists intentionally deferred improvements — not forgotten bugs.
Each item describes the current state, the reason for deferral, and the planned solution.

---

## 1. PWA Screenshots

**Status:** Planned

**Reason:**
The Web App Manifest does not include the `screenshots` field because production screenshots have not yet been created. Without real screenshots, adding placeholder paths would result in broken references in the manifest.

**Planned solution:**
1. Capture real application screens (menu view, cart view) on a 390×844 viewport.
2. Save as `/static/screenshot-menu.png` and `/static/screenshot-cart.png`.
3. Add the `screenshots` array to `manifest.json`:
   ```json
   "screenshots": [
     {
       "src": "/static/screenshot-menu.png",
       "sizes": "390x844",
       "type": "image/png",
       "form_factor": "narrow",
       "label": "Taomly — menyu va buyurtma"
     },
     {
       "src": "/static/screenshot-cart.png",
       "sizes": "390x844",
       "type": "image/png",
       "form_factor": "narrow",
       "label": "Taomly — savat va to'lov"
     }
   ]
   ```

**Impact:** Chrome 119+ shows screenshots in the PWA install prompt. Without them, the install banner appears with no preview. The app remains fully installable via the browser's native UI.

---

## 2. xBadge Backward Compatibility

**Status:** Intentional backward compatibility

**Reason:**
The frontend derives product badges (`Bestseller`, `Chef's Choice`, `New Arrival`, `Spicy`) from product descriptions using `xBadge()`, which parses hashtag syntax — e.g. `#bestseller` — embedded in the description field.

The backend already exposes dedicated boolean fields (`is_bestseller`, `is_new`, `is_spicy`, `is_chef_choice`) in the product model, but the frontend continues using `xBadge()` to remain compatible with existing restaurant data that was entered using the hashtag convention.

**Future work:**
1. Migrate existing product descriptions to remove embedded hashtags.
2. Switch `makeCard()` and `renderCart()` in `index.html` to read boolean flags directly from the API response.
3. Remove `xBadge()` and the `BM` constant.

**Impact:** No functional regression. Restaurants using the hashtag convention continue to see badges correctly.

---

## 3. PWA Install Banner

**Status:** Deferred

**Reason:**
The `beforeinstallprompt` event is captured and stored in `_dip` as a deliberate placeholder. The browser's default install prompt is suppressed, but no custom UI has been built to trigger it.

The application remains fully installable via the browser's native install flow (address bar install button, browser menu).

**Future work:**
1. Design an in-app install prompt (e.g. a bottom sheet or sticky banner).
2. Show it after the user has viewed the menu at least once.
3. Call `_dip.prompt()` on user confirmation.
4. Track install outcome via `_dip.userChoice`.

**Impact:** Users on Android Chrome do not see a custom "Add to Home Screen" prompt. Install rates may be lower than with an explicit in-app banner.

---

## 4. async SQLAlchemy Migration

**Status:** Deferred — requires explicit approval before starting

**Reason:**
The backend currently uses synchronous SQLAlchemy with a thread pool. Migrating to `asyncpg` + async SQLAlchemy would improve throughput under concurrent load but requires rewriting all database access layers, service functions, and tests.

**Planned sprint:** Separate dedicated sprint. Do not start without explicit sign-off.

**Impact:** No current production issues. Render Free tier constraints make this a lower priority than stability and feature completeness.
