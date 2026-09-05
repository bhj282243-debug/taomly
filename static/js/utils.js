// ── UTILS.JS — Utility & helper functions (R-3.2) ────────────────────────────
// Зависит от: state.js (restaurant, для _getCurrency)
// Не содержит menu/cart/order business logic.

// ── PRICE FORMATTER ──────────────────────────────────────────────────────────
// Единый форматтер цен. Использует restaurant.currency, загруженный из API.
// Поддерживаемые валюты: UZS, KZT, RUB, USD, TRY, AED.
// Дефолт 'UZS' обеспечивает обратную совместимость.
const _CURRENCY_FMT={
  UZS:{pre:'',  suf:" so\u2019m",dec:0},
  KZT:{pre:'',  suf:' \u20b8',   dec:0},
  RUB:{pre:'',  suf:' \u20bd',   dec:0},
  USD:{pre:'$', suf:'',          dec:2},
  TRY:{pre:'\u20ba',suf:'',     dec:2},
  AED:{pre:'AED ',suf:'',        dec:2},
};
function _getCurrency(){return (restaurant&&restaurant.currency)||'UZS';}

function fmt(p){
  const c=_getCurrency();
  const f=_CURRENCY_FMT[c]||_CURRENCY_FMT.UZS;
  if(f.dec===0){
    const s=Math.round(p).toLocaleString('ru-RU');
    return f.pre+s+f.suf;
  }else{
    return f.pre+parseFloat(p).toFixed(f.dec)+f.suf;
  }
}
function fmtNum(p){return Math.round(p).toLocaleString('ru-RU');}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

// ── COLOR HELPERS (используются в theme.js) ───────────────────────────────────
function _rgba(h,a){if(!h||h.length<7)return`rgba(122,92,16,${a})`;const r=parseInt(h.slice(1,3),16),g=parseInt(h.slice(3,5),16),b=parseInt(h.slice(5,7),16);return`rgba(${r},${g},${b},${a})`;}
function _lum(h){if(!h||h.length<7)return 0;return[h.slice(1,3),h.slice(3,5),h.slice(5,7)].map(x=>{const v=parseInt(x,16)/255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);}).reduce((s,v,i)=>s+[0.2126,0.7152,0.0722][i]*v,0);}
function _ct(a,b){const la=_lum(a),lb=_lum(b);return(Math.max(la,lb)+0.05)/(Math.min(la,lb)+0.05);}
function _ec(h,bg,min){let c=h,n=0;while(_ct(c,bg)<min&&n<24){c=_dk(c,10);n++;}return c;}
function _lk(h,a){if(!h||h.length<7)return h;return'#'+[parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)].map(v=>Math.min(255,v+a).toString(16).padStart(2,'0')).join('');}
function _dk(h,a){if(!h||h.length<7)return h;return'#'+[parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)].map(v=>Math.max(0,v-a).toString(16).padStart(2,'0')).join('');}

// ── MENU DISPLAY HELPERS ──────────────────────────────────────────────────────
// cleanDesc — убирает legacy #hashtag из description для безопасного отображения
function cleanDesc(d){
  if(!d)return'';
  return d.replace(/#(bestseller|chef|new|spicy)\b/gi,'').trim();
}

// safePrice — безопасное отображение цены продукта (legacy или variant)
function safePrice(p){
  const variants=Array.isArray(p.variants)?p.variants:[];
  if(variants.length>0){
    const prices=variants.map(v=>v.price).filter(x=>typeof x==='number'&&isFinite(x));
    if(!prices.length)return'';
    const minP=Math.min(...prices);
    const maxP=Math.max(...prices);
    if(minP===maxP)return fmt(minP);
    return t('menu.from_price')||('от '+fmt(minP));
  }
  if(p.price==null)return'';
  return fmt(p.price);
}
