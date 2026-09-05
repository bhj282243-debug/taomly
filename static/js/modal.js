// ── MODAL.JS — Product Modal (R-3.3) ─────────────────────────────────────────
// Зависит от: state.js, utils.js, i18n.js, menu.js (findProduct, getBadge,
//             cleanDesc, getCartKey, addLegacyItem, addVariantItem, changeQty)
// Содержит: openProductModal, closeProductModal, refreshModalCtrl,
//           _renderVariantPicker, _vpSelect, _vpConfirm, _renderModifierGroups

// ── MODIFIER GROUPS ───────────────────────────────────────────────────────────

// _renderModifierGroups — Phase 5: рендерит секцию modifier groups в modal-body
// Вставляется между .modal-desc и .modal-footer. Удаляет предыдущую секцию при каждом вызове.
function _renderModifierGroups(p){
  const existing=document.getElementById('p5ModifierSection');
  if(existing)existing.remove();

  const groups=Array.isArray(p.modifier_groups)?p.modifier_groups:[];
  if(!groups.length)return;

  const currency=_getCurrency();
  let html='<div id="p5ModifierSection" style="margin-bottom:8px">';
  groups.forEach((g,gi)=>{
    const isRequired=g.min_selections>0;
    const reqLabel=isRequired
      ?`<span class="mg-req">${t('modifier.required')||'Majburiy'}</span>`
      :`<span class="mg-opt">${t('modifier.optional')||'Ixtiyoriy'}</span>`;

    html+=`<div class="mg-section">`;
    html+=`<div class="mg-header"><span class="mg-name">${esc(g.name)}</span>${reqLabel}</div>`;
    html+=`<div class="mg-options">`;

    const options=Array.isArray(g.options)?g.options:[];
    options.forEach(o=>{
      const unavail=o.is_available===false;
      const pricePart=o.price_adjustment>0
        ?`<span class="mo-price">+${fmt(o.price_adjustment)}</span>`
        :(o.price_adjustment<0?`<span class="mo-price">${fmt(o.price_adjustment)}</span>`:'');
      const unavailBadge=unavail?`<span class="mo-unavail-badge">${t('menu.sold_out')||'Tugagan'}</span>`:'';
      html+=`<div class="mo-item${unavail?' mo-unavailable':''}">
        <span class="mo-name">${esc(o.name)}${unavailBadge}</span>
        ${pricePart}
      </div>`;
    });

    html+=`</div></div>`;
    if(gi<groups.length-1)html+=`<div class="mg-divider"></div>`;
  });
  html+='</div>';

  const footer=document.querySelector('.modal-footer');
  if(footer)footer.insertAdjacentHTML('beforebegin',html);
}

// ── VARIANT PICKER ────────────────────────────────────────────────────────────

// _renderVariantPicker — рендерит picker вариантов внутри modal footer
function _renderVariantPicker(p,variants){
  const priceEl=document.getElementById('productModalPrice');
  const currEl=document.getElementById('modalCurrLabel');
  if(priceEl)priceEl.textContent='';
  if(currEl)currEl.textContent='';
  const priceBlock=document.getElementById('modalPriceBlock');
  if(priceBlock)priceBlock.style.display='none';

  const footer=document.querySelector('.modal-footer');
  if(footer){footer.style.flexDirection='column';footer.style.alignItems='stretch';footer.style.gap='0';}

  let listHtml='<div class="vp-list" id="vpList">';
  variants.forEach(v=>{
    // Phase 3: is_available=false → visible but disabled ("Sold out")
    const soldOut=v.is_available===false;
    const itemCls='vp-item'+(soldOut?' vp-item-soldout':'');
    const clickHandler=soldOut?'':'onclick="_vpSelect('+p.id+','+v.id+')"';
    const soldBadge=soldOut?'<span class="vp-soldout-badge">Sold out</span>':'';
    listHtml+=`<div class="${itemCls}" id="vp-${v.id}" ${clickHandler}>
      <div class="vp-radio"><div class="vp-radio-dot"></div></div>
      <span class="vp-name">${esc(v.name)}</span>
      <span class="vp-price">${v.price!=null?fmt(v.price):''}${soldBadge}</span>
    </div>`;
  });
  listHtml+='</div>';

  const btnHtml=`<button class="vp-add-btn" id="vpAddBtn" disabled onclick="_vpConfirm(${p.id})">
    <svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
    <span id="vpAddBtnText">${t('modal.add')||'Добавить'}</span>
  </button>`;

  document.getElementById('productModalCtrl').innerHTML=listHtml+btnHtml;
}

// ── VARIANT SELECTION ─────────────────────────────────────────────────────────

// _vpSelect — выбрать вариант
function _vpSelect(productId,variantId){
  if(productModalCurrentId!==productId)return;
  _vpSelectedVariantId=variantId;
  document.querySelectorAll('.vp-item').forEach(el=>el.classList.remove('selected'));
  const sel=document.getElementById('vp-'+variantId);
  if(sel)sel.classList.add('selected');
  const btn=document.getElementById('vpAddBtn');
  if(btn){
    btn.disabled=false;
    const p=findProduct(productId);
    const v=p?.variants?.find(v=>v.id===variantId);
    const btnText=document.getElementById('vpAddBtnText');
    if(btnText&&v)btnText.textContent=(t('modal.add')||'Добавить')+(v.price!=null?' · '+fmt(v.price):'');
  }
}

// _vpConfirm — подтвердить выбор и добавить в корзину
function _vpConfirm(productId){
  if(_vpSelectedVariantId==null)return;
  const p=findProduct(productId);if(!p)return;
  const variants=Array.isArray(p.variants)?p.variants:[];
  const v=variants.find(v=>v.id===_vpSelectedVariantId);if(!v)return;
  addVariantItem(productId,v);
  closeProductModal();
}

// ── MODAL CTRL REFRESH ────────────────────────────────────────────────────────

function refreshModalCtrl(){
  const id=productModalCurrentId;
  const p=findProduct(id);if(!p)return;
  const variants=Array.isArray(p.variants)?p.variants:[];
  if(variants.length>0)return; // variant picker не обновляем через refreshModalCtrl
  const key=getCartKey(id,null);
  const qty=cart[key]?.qty||0;
  const ctrl=document.getElementById('productModalCtrl');
  const footer=document.querySelector('.modal-footer');
  if(footer){footer.style.flexDirection='';footer.style.alignItems='';footer.style.gap='';}
  const priceBlock=document.getElementById('modalPriceBlock');
  if(priceBlock)priceBlock.style.display='';
  if(qty===0){ctrl.innerHTML=`<button class="modal-add-btn" onclick="addLegacyItem(${id});refreshModalCtrl()"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg> ${t('modal.add')||'Добавить'}</button>`;}
  else{const key2=getCartKey(id,null);ctrl.innerHTML=`<div class="modal-qty"><button class="qty-ring" style="width:42px;height:42px" onclick="changeQty('${key2}',-1);refreshModalCtrl()"><svg class="icon" viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button><span class="qty-digit" style="font-size:22px;min-width:28px">${qty}</span><button class="qty-ring" style="width:42px;height:42px" onclick="changeQty('${key2}',1);refreshModalCtrl()"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button></div>`;}
}

// ── OPEN / CLOSE MODAL ────────────────────────────────────────────────────────

function openProductModal(id){
  const p=findProduct(id);if(!p)return;
  productModalCurrentId=id;
  _vpSelectedVariantId=null;
  const variants=Array.isArray(p.variants)?p.variants:[];
  // Phase 5: use boolean badge fields
  const{badge,cls:badgeCls}=getBadge(p);
  const dt=cleanDesc(p.description);

  const ph=document.getElementById('productModalPhoto');
  if(p.photo_url){
    ph.className='modal-photo tall';
    ph.innerHTML=`<img src="${esc(p.photo_url)}" alt="${esc(p.name)}" loading="lazy" decoding="async"><div class="modal-grad"></div>`;
  }else{
    ph.className='modal-photo tall';
    ph.innerHTML=`<div class="modal-ph"><div class="modal-ph-crest"><svg class="icon" viewBox="0 0 24 24"><path d="M18 8a4 4 0 0 1 0 8M6 4v16M6 4c0 2.5 2.5 2.5 2.5 5S6 11.5 6 14"/></svg></div></div>`;
  }

  document.getElementById('productModalBadge').innerHTML=badge?`<span class="${badgeCls}">${badge}</span>`:'';
  document.getElementById('productModalName').textContent=p.name;
  document.getElementById('productModalDesc').textContent=dt||'';

  // Phase 5: render modifier groups BEFORE variant picker / add button
  _renderModifierGroups(p);

  if(variants.length>0){
    _renderVariantPicker(p,variants);
  }else{
    const priceStr=p.price!=null?fmtNum(p.price):'';
    document.getElementById('productModalPrice').textContent=priceStr;
    refreshModalCtrl();
  }

  document.getElementById('productModalOverlay').classList.add('active');
}

function closeProductModal(){
  document.getElementById('productModalOverlay').classList.remove('active');
  productModalCurrentId=null;
  _vpSelectedVariantId=null;
  const footer=document.querySelector('.modal-footer');
  if(footer){footer.style.flexDirection='';footer.style.alignItems='';footer.style.gap='';}
  const priceBlock=document.getElementById('modalPriceBlock');
  if(priceBlock)priceBlock.style.display='';
  // Phase 5: удалить секцию modifier groups
  const modSec=document.getElementById('p5ModifierSection');
  if(modSec)modSec.remove();
}
