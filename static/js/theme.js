// ── THEME.JS — Brand theme application (R-3.2) ───────────────────────────────
// Зависит от: state.js (restaurantSlug), utils.js (_rgba, _lk, _ec, _CURRENCY_FMT)

function applyBrandTheme(r){
  const root=document.documentElement;
  const hex=r.primary_color||r.accent_color||null;
  if(hex){
    const s=_ec(hex,'#F4F1EA',3.0);
    root.style.setProperty('--g',s);
    root.style.setProperty('--g2',_lk(s,10));
    root.style.setProperty('--g3',_lk(s,24));
    root.style.setProperty('--gt',_rgba(s,.08));
    root.style.setProperty('--gtm',_rgba(s,.14));
    root.style.setProperty('--gtl',_rgba(s,.22));
  }
  if(r.name){
    const pt=document.getElementById('pwaTitle');if(pt)pt.setAttribute('content',r.name);
    document.title=r.name;
    const sn=document.getElementById('splashName');if(sn)sn.textContent=r.name.toUpperCase();
    const sl=document.getElementById('splashLetter');if(sl)sl.textContent=r.name[0].toUpperCase();
  }
  document.getElementById('pwaThemeColor').setAttribute('content',r.primary_color||'#F4F1EA');
  // Обновляем label валюты в модальном окне продукта
  const _mcl=document.getElementById('modalCurrLabel');
  if(_mcl){const _cf=_CURRENCY_FMT[r.currency||'UZS']||_CURRENCY_FMT.UZS;_mcl.textContent=(_cf.pre||'')+(_cf.suf||'').trim();}
  if(restaurantSlug){const ml=document.getElementById('pwaManifest');if(ml)ml.setAttribute('href',`/manifest/${restaurantSlug}.json`);}
}
