// ── MENU.JS — Menu rendering (R-3.3) ─────────────────────────────────────────
// Зависит от: state.js, utils.js, i18n.js, theme.js, api.js
// Содержит: loadMenu, showHero, enterMenu, renderMenu, renderCategories,
//           renderPopular, makeCard, ctrlHtml, cardQty, getBadge, cleanDesc(*),
//           xBadge, findProduct, refreshCtrl, resolveTableId
// (*) cleanDesc уже в utils.js — здесь НЕ дублируется.

// ── HERO ──────────────────────────────────────────────────────────────────────

function showHero(r){
  const L=(r.name||'T')[0].toUpperCase();
  const med=document.getElementById('heroMed');
  if(r.logo_url){med.innerHTML=`<img src="${esc(r.logo_url)}" alt="${esc(r.name)}"`+'>';}
  else{document.getElementById('heroMedL').textContent=L;}
  document.getElementById('heroName').textContent=r.name||'';
  if(r.address){document.getElementById('heroAddrText').textContent=r.address;document.getElementById('heroAddr').style.display='inline-flex';}
  if(r.working_hours){document.getElementById('heroHoursText').textContent=r.working_hours;document.getElementById('heroHours').style.display='inline-flex';}
  document.getElementById('heroScreen').classList.add('active');
}

function enterMenu(){
  const h=document.getElementById('heroScreen');
  h.style.transition='opacity .45s ease';h.style.opacity='0';
  setTimeout(()=>{h.style.display='none';document.getElementById('menuScreen').style.display='block';},450);
}

// ── LOAD MENU (orchestration) ─────────────────────────────────────────────────

async function loadMenu(){
  if(!restaurantSlug){
    const s=document.getElementById('splash');if(s)s.style.display='none';
    const msg=t('error.not_found')||'Restoran topilmadi.\nIltimos, QR-kodni skanerlang.';
    const retry=t('error.retry')||'Qayta urinish';
    document.body.insertAdjacentHTML('beforeend',`<div style="position:fixed;inset:0;z-index:9999;background:#F4F1EA;display:flex;align-items:center;justify-content:center;padding:32px;text-align:center"><div style="color:#7A5C10;font-size:15px;line-height:1.8">${esc(msg).replace('\n','<br>')}<br><span style="font-size:48px;display:block;margin:18px 0">📷</span></div></div>`);
    return;
  }
  try{
    const res=await fetch(`${API_BASE}/api/restaurants/${restaurantSlug}`);
    if(!res.ok)throw new Error(t('error.server')||'Server xatosi');
    restaurant=await res.json();
    await loadI18n(restaurant.language);
    applyI18n();
    applyBrandTheme(restaurant);
    document.getElementById('restaurantName').textContent=restaurant.name;
    const lw=document.getElementById('restaurantLogo');
    if(restaurant.logo_url){lw.innerHTML=`<img src="${esc(restaurant.logo_url)}" alt="${esc(restaurant.name)}">`;}
    else{const L=esc((restaurant.name||'T')[0].toUpperCase());lw.innerHTML=`<div class="rest-emb-L">${L}</div>`;}
    if(restaurant.address){document.getElementById('restaurantAddressText').textContent=restaurant.address;document.getElementById('restaurantAddress').style.display='flex';}
    if(restaurant.welcome_text){document.getElementById('welcomeText').textContent=restaurant.welcome_text;document.getElementById('welcomeLine').style.display='block';}
    if(tableNumber){document.getElementById('tableBadgeText').textContent=t('cart.table_label',{n:tableNumber});document.getElementById('tableBadge').style.display='inline-flex';await resolveTableId();}
    renderPopular();renderCategories();renderMenu();
    setTimeout(()=>{
      document.getElementById('splash').classList.add('out');
      setTimeout(()=>{
        const s=document.getElementById('splash');if(s)s.style.display='none';
        if(orderType==='dine_in'){document.getElementById('menuScreen').style.display='block';}
        else{showHero(restaurant);}
      },600);
    },800);
  }catch(e){
    const s=document.getElementById('splash');if(s)s.style.display='none';
    const retry=t('error.retry')||'Qayta urinish';
    document.body.insertAdjacentHTML('beforeend',`<div style="position:fixed;inset:0;z-index:9999;background:#F4F1EA;display:flex;align-items:center;justify-content:center;padding:32px;text-align:center"><div style="color:#7A5C10;font-size:15px;line-height:1.8">${esc(e.message)}<br><br><button onclick="location.reload()" style="margin-top:16px;padding:12px 32px;background:#7A5C10;color:#FAF8F3;border:none;border-radius:100px;font-size:14px;font-weight:600;cursor:pointer">${esc(retry)}</button></div></div>`);
  }
}

// ── TABLE RESOLVER ────────────────────────────────────────────────────────────

async function resolveTableId(){
  try{const r=await fetch(`${API_BASE}/api/restaurants/${restaurantSlug}/table/${tableNumber}`);if(r.ok){const d=await r.json();tableDbId=d.table_id;}}
  catch(e){tableDbId=null;}
}

// ── CATEGORIES NAV ────────────────────────────────────────────────────────────

function renderCategories(){
  const nav=document.getElementById('categoriesNav');nav.innerHTML='';
  (restaurant.categories||[]).forEach((cat,i)=>{
    const btn=document.createElement('button');btn.className='cat-btn'+(i===0?' active':'');
    btn.textContent=cat.name;
    btn.onclick=()=>{document.querySelectorAll('.cat-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');const el=document.getElementById('cat-'+cat.id);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});};
    nav.appendChild(btn);
  });
}

// ── POPULAR SECTION ───────────────────────────────────────────────────────────

function renderPopular(){
  const sec=document.getElementById('popularSection'),sc=document.getElementById('popularScroll');
  const items=[];
  (restaurant.categories||[]).forEach(c=>(c.products||[]).forEach(p=>{if(p.is_popular)items.push(p);}));
  if(!items.length){sec.style.display='none';return;}
  sec.style.display='block';sc.innerHTML='';
  items.forEach(p=>{
    const el=document.createElement('div');el.className='pop-item';el.onclick=()=>openProductModal(p.id);
    const img=p.photo_url
      ?`<img src="${p.photo_url}" alt="${p.name}" loading="lazy" decoding="async">`
      :`<div class="pop-ph"><div class="pop-ph-diamond"><svg class="icon" viewBox="0 0 24 24"><path d="M18 8a4 4 0 0 1 0 8M6 4v16M6 4c0 2.5 2.5 2.5 2.5 5S6 11.5 6 14"/></svg></div></div>`;
    el.innerHTML=`<div class="pop-photo">${img}</div><div class="pop-name">${esc(p.name)}</div><div class="pop-price">${safePrice(p)}</div>`;
    sc.appendChild(el);
  });
}

// ── MENU RENDER ───────────────────────────────────────────────────────────────

function renderMenu(){
  const c=document.getElementById('menuContent');c.innerHTML='';_ai=0;
  const allCats=restaurant.categories||[];
  if(!allCats.length){
    c.innerHTML=`<div class="cat-empty">${esc(t('menu.empty_menu')||'Menyu hozircha bo\'sh')}</div>`;
    return;
  }
  allCats.forEach(cat=>{
    const sec=document.createElement('div');sec.className='cat-sec';sec.id='cat-'+cat.id;
    const products=cat.products||[];
    const prodCountHtml=products.length?`<span class="cat-cnt">${t('menu.product_count',{n:products.length})}</span>`:'';
    sec.innerHTML=`<div class="cat-head"><div class="cat-title">${esc(cat.name)}${prodCountHtml}</div></div><div class="prod-grid" id="grid-${cat.id}"></div>`;
    c.appendChild(sec);
    const grid=sec.querySelector('#grid-'+cat.id);
    if(!products.length){
      grid.innerHTML=`<div class="cat-empty" style="padding:20px">${esc(t('menu.empty_category')||'Bu kategoriyada mahsulot yo\'q')}</div>`;
    }else{
      products.forEach(p=>grid.appendChild(makeCard(p)));
    }
  });
}

// ── BADGE ─────────────────────────────────────────────────────────────────────

// Phase 5: getBadge — читает boolean поля is_bestseller/is_new/is_spicy/is_chef_choice
// напрямую из продукта. Приоритет: spicy > new > bestseller > chef.
function getBadge(p){
  if(p.is_spicy)return{badge:'🌶 Spicy',cls:'p5-badge-spicy'};
  if(p.is_new)return{badge:'New',cls:'p5-badge-new'};
  if(p.is_bestseller)return{badge:'Bestseller',cls:'p5-badge-bestseller'};
  if(p.is_chef_choice)return{badge:"Chef's Choice",cls:'p5-badge-chef'};
  return{badge:null,cls:''};
}

// xBadge — оставляем для совместимости с renderCart() (корзина — не Phase 5 scope)
const BM={bestseller:'Bestseller',chef:"Chef's Choice",new:'New Arrival',spicy:'🌶 Spicy'};
function xBadge(d){
  if(!d)return{badge:null,text:''};
  const m=d.match(/#(bestseller|chef|new|spicy)\b/i);
  if(!m)return{badge:null,text:d};
  return{badge:BM[m[1].toLowerCase()],text:d.replace(m[0],'').trim()};
}

// ── CARD QUANTITY ─────────────────────────────────────────────────────────────

// cardQty — суммарное кол-во продукта в корзине для отображения на карточке
// Для legacy — cart[productId].qty
// Для variant product — нет единого qty (несколько вариантов независимы)
function cardQty(p){
  const variants=Array.isArray(p.variants)?p.variants:[];
  if(variants.length>0)return null; // null = показывать только Add кнопку
  return cart[getCartKey(p.id,null)]?.qty||0;
}

// ── CARD HTML ─────────────────────────────────────────────────────────────────

function makeCard(p){
  const div=document.createElement('div');
  div.id='card-'+p.id;
  div.style.animationDelay=(_ai++%10)*0.05+'s';
  const qty=cardQty(p); // null для variant products
  // Phase 5: boolean badge fields (is_bestseller, is_new, is_spicy, is_chef_choice)
  const{badge,cls:badgeCls}=getBadge(p);
  const dt=cleanDesc(p.description);
  const bh=badge?`<div class="pphoto-badge ${badgeCls}">${badge}</div>`:'';
  const priceHtml=safePrice(p);
  // Phase 5: sold-out — is_available=false on a non-variant product
  const variants=Array.isArray(p.variants)?p.variants:[];
  const isSoldOut=!p.is_available&&variants.length===0;

  if(p.photo_url){
    div.className='pcard has-photo'+(isSoldOut?' p5-soldout':'');
    if(!isSoldOut)div.onclick=()=>openProductModal(p.id);
    div.innerHTML=`
      <div class="pphoto-wrap">
        <img src="${esc(p.photo_url)}" alt="${esc(p.name)}" loading="lazy" decoding="async">
        ${isSoldOut?`<div style="position:absolute;top:14px;right:14px"><span class="p5-soldout-overlay">${t('menu.sold_out')||'Tugagan'}</span></div>`:bh}
      </div>
      <div class="pphoto-content">
        <div class="pphoto-text">
          <div class="pname">${esc(p.name)}</div>
          ${dt?`<div class="pdesc-photo">${esc(dt)}</div>`:''}
          ${priceHtml?`<div class="pprice-photo">${priceHtml}</div>`:''}
        </div>
        <div class="photo-add" id="ctrl-${p.id}" onclick="event.stopPropagation()">${isSoldOut?'':ctrlHtml(p,qty)}</div>
      </div>`;
  }else{
    div.className='pcard no-photo'+(isSoldOut?' p5-soldout':'');
    if(!isSoldOut)div.onclick=()=>openProductModal(p.id);
    div.innerHTML=`
      <div class="pphoto-wrap">
        <div class="pphoto-ph">
          <div class="pphoto-ph-center">
            <div class="ph-crest"><svg class="icon" viewBox="0 0 24 24"><path d="M18 8a4 4 0 0 1 0 8M6 4v16M6 4c0 2.5 2.5 2.5 2.5 5S6 11.5 6 14"/></svg></div>
            <div class="ph-label">${t('menu.fine_dining')}</div>
          </div>
        </div>
        <div class="pphoto-overlay"></div>${isSoldOut?'':bh}
        <div class="pphoto-content">
          <div class="pphoto-text">
            ${badge&&!isSoldOut?`<div class="pphoto-badge ${badgeCls}" style="position:static;margin-bottom:6px">${badge}</div>`:''}
            ${isSoldOut?`<span class="p5-soldout-overlay" style="display:inline-flex;margin-bottom:6px">${t('menu.sold_out')||'Tugagan'}</span>`:''}
            <div class="pname">${esc(p.name)}</div>
            ${dt?`<div class="pdesc-photo">${esc(dt)}</div>`:''}
            ${priceHtml?`<div class="pprice-photo">${priceHtml}</div>`:''}
          </div>
          <div class="photo-add" id="ctrl-${p.id}" onclick="event.stopPropagation()">${isSoldOut?'':ctrlHtml(p,qty)}</div>
        </div>
      </div>`;
  }
  return div;
}

// ── CTRL HTML ─────────────────────────────────────────────────────────────────

// ctrlHtml — qty=null означает variant product (всегда показывать Add → picker)
function ctrlHtml(p,qty){
  const variants=Array.isArray(p.variants)?p.variants:[];
  if(variants.length>0){
    return`<button class="add-ring" onclick="event.stopPropagation();openProductModal(${p.id})"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>`;
  }
  if(!qty)return`<button class="add-ring" onclick="event.stopPropagation();addLegacyItem(${p.id})"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>`;
  const key=getCartKey(p.id,null);
  return`<div class="qty-unit"><button class="qty-ring" onclick="changeQty('${key}',-1)"><svg class="icon" viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button><span class="qty-digit">${qty}</span><button class="qty-ring" onclick="changeQty('${key}',1)"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button></div>`;
}

// ── FIND PRODUCT ──────────────────────────────────────────────────────────────

function findProduct(id){for(const c of(restaurant?.categories||[])){const p=c.products.find(p=>p.id===id);if(p)return p;}return null;}

// ── REFRESH CTRL ──────────────────────────────────────────────────────────────

function refreshCtrl(productId){
  const el=document.getElementById('ctrl-'+productId);if(!el)return;
  const p=findProduct(productId);if(!p)return;
  el.innerHTML=ctrlHtml(p,cardQty(p));
}
