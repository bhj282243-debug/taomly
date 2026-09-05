// ── I18N.JS — Frontend translation loader (R-3.2) ────────────────────────────
// PHASE 3.2: Frontend translation loader.
// Language source: restaurant.language ONLY.
// Currency (restaurant.currency) is independent — not affected here.
// Зависит от: state.js (нет прямых зависимостей от state vars здесь)

window._i18n={};
window._lang='uz';

function t(key,vars){
  const val=window._i18n[key]||key;
  if(!vars)return val;
  return val.replace(/\{\{(\w+)\}\}/g,(_,k)=>vars[k]!=null?vars[k]:'');
}

async function loadI18n(lang){
  const allowed=['uz','ru','en'];
  window._lang=allowed.includes(lang)?lang:'uz';
  try{
    const res=await fetch(`/i18n/${window._lang}.json`);
    if(!res.ok)throw new Error('i18n fetch failed');
    window._i18n=await res.json();
  }catch(e){
    // Fallback: keep whatever is already loaded (or empty = keys shown as-is)
    console.warn('[i18n] Failed to load',window._lang,e.message);
    if(window._lang!=='uz'){
      // Try UZ fallback
      try{const fb=await fetch('/i18n/uz.json');if(fb.ok)window._i18n=await fb.json();}
      catch(_){}
    }
  }
}

function applyI18n(){
  document.documentElement.lang=window._lang;
  document.querySelectorAll('[data-i18n]').forEach(el=>{
    const key=el.getAttribute('data-i18n');
    el.textContent=t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el=>{
    const key=el.getAttribute('data-i18n-placeholder');
    el.placeholder=t(key);
  });
}
