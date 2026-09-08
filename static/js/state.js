// ── STATE.JS — Global application state (R-3.2) ──────────────────────────────
// Все глобальные переменные приложения. Загружается первым.
// Не переименовывать — inline onclick и остальной JS зависят от этих имён.

const API_BASE='';

let restaurant=null;
let cart={};
let orderType='delivery';
let tableNumber=null;
let tableDbId=null;
let restaurantSlug=null;

// Phase 6: Cart session identity.
// session_id: opaque UUID4, generated once per device, stored in localStorage.
// locationId: set after restaurant is loaded (from restaurant.location_id).
let _cartSessionId=null;
let _cartLocationId=null;

function getOrCreateCartSessionId(){
  if(_cartSessionId)return _cartSessionId;
  try{
    let sid=localStorage.getItem('cart_session_id');
    if(!sid||(typeof sid==='string'&&sid.length<30)){
      // Generate UUID4 using crypto.randomUUID (supported in all target browsers)
      sid=(typeof crypto!=='undefined'&&crypto.randomUUID)?crypto.randomUUID()
        :([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,c=>(c^(crypto.getRandomValues(new Uint8Array(1))[0]&(15>>c/4))).toString(16));
      localStorage.setItem('cart_session_id',sid);
    }
    _cartSessionId=sid;
  }catch(e){
    // localStorage unavailable (private mode etc.) — use in-memory fallback
    if(!_cartSessionId){
      _cartSessionId='mem-'+Math.random().toString(36).slice(2)+Math.random().toString(36).slice(2);
    }
  }
  return _cartSessionId;
}

function _cartHeaders(){
  const tg=window.Telegram?.WebApp;
  const h={
    'Content-Type':'application/json',
    'X-Restaurant-Id':String(restaurant?.id||''),
    'X-Location-Id':String(_cartLocationId||''),
    'X-Cart-Session':getOrCreateCartSessionId(),
  };
  if(tg?.initData)h['X-Telegram-Init-Data']=tg.initData;
  return h;
}

// Menu rendering
let _ai=0;

// Modal state
let productModalCurrentId=null;
let _vpSelectedVariantId=null;

// Order polling
let _pollingTimer=null;
let _pollingOrderId=null;
let _pollingHeaders=null;

// Toast timer
let _tt=null;

// PWA install prompt
let _dip=null;
