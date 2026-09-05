// ── APP.JS — Application bootstrap & initialization (R-3.4B) ─────────────────
// Загружается последним. Зависит от всех предыдущих модулей.
// Содержит: parseParams, DOMContentLoaded init, Telegram init,
//           service worker registration, PWA install prompt

// ── PARAMS PARSER ─────────────────────────────────────────────────────────────

function parseParams(){
  const u=new URLSearchParams(window.location.search);
  const sp=u.get('slug'),tp=u.get('table'),typ=u.get('type');
  if(sp){restaurantSlug=sp;if(tp){tableNumber=tp;orderType='dine_in';}else if(typ){orderType=typ;}return;}
  const tg=window.Telegram?.WebApp;
  const s=tg?.initDataUnsafe?.start_param||'';
  if(s){
    const p=s.split('_');
    if(p.length>=2&&!isNaN(p[p.length-1])){tableNumber=p[p.length-1];restaurantSlug=p.slice(0,-1).join('_');orderType='dine_in';}
    else{restaurantSlug=s;}
  }
}

// ── BOOTSTRAP ─────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded',()=>{
  const tg=window.Telegram?.WebApp;if(tg){tg.ready();tg.expand();}
  parseParams();loadMenu();
});

// ── SERVICE WORKER ────────────────────────────────────────────────────────────

if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/sw.js')
      .then(r=>console.log('[SW]',r.scope))
      .catch(e=>console.warn('[SW]',e));
  });
}

// ── PWA INSTALL PROMPT ────────────────────────────────────────────────────────
// _dip объявлен в state.js

window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();_dip=e;});
window.addEventListener('appinstalled',()=>{_dip=null;});
