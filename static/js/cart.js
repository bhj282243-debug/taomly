// ── CART.JS — Cart logic (R-3.4A) ────────────────────────────────────────────
// Зависит от: state.js (cart, orderType, tableNumber, tableDbId, restaurant),
//             utils.js (fmt, esc), i18n.js (t), menu.js (refreshCtrl)
// Содержит: getCartKey, addLegacyItem, addVariantItem, changeQty,
//           cartItems, cartTotal, cartCount, updateBar,
//           openCart, closeCart, renderCart, cartChangeQty,
//           selectType, _updateDeliveryRow

// ── CART KEY ──────────────────────────────────────────────────────────────────
// Legacy product:  "123"
// Variant product: "123__456"

function getCartKey(productId,variantId){
  return variantId!=null?String(productId)+'__'+String(variantId):String(productId);
}

// ── SERVER CART SYNC (Phase 6) ────────────────────────────────────────────────
// Server Cart is authoritative. Local cart = optimistic UI cache.
// add/remove: blocking (await server response).
// quantity update: optimistic + rollback on failure.
// Page load: hydrates local cart from server.

// _serverCartItemIds: map from cartKey → server CartItem id (for PATCH/DELETE)
let _serverCartItemIds={};

function _buildLocalFromServer(serverCart){
  // Rebuild local cart={} from CartResponse
  const newCart={};
  const newIds={};
  (serverCart.items||[]).forEach(item=>{
    const key=getCartKey(item.product_id,item.variant_id||null);
    newCart[key]={
      id:item.product_id,
      variant_id:item.variant_id||null,
      name:item.product_name||'',
      variant_name:item.variant_name||null,
      price:item.unit_price,
      qty:item.quantity,
      photo:null, // photos come from local product data
      _server_item_id:item.id,
    };
    newIds[key]=item.id;
    // Restore photo from local product data if available
    const localProd=findProduct?findProduct(item.product_id):null;
    if(localProd)newCart[key].photo=localProd.photo_url||null;
  });
  return{cart:newCart,ids:newIds};
}

async function syncCartFromServer(){
  // Called on page load to hydrate local cart from server state.
  if(!restaurant||!_cartLocationId)return;
  try{
    const headers=_cartHeaders();
    // GET /api/cart doesn't require X-Location-Id strictly, but send it anyway
    const r=await fetch(`${API_BASE}/api/cart`,{method:'GET',headers});
    if(!r.ok)return;
    const serverCart=await r.json();
    if(serverCart.cart_id===0){
      // No server cart exists — local state is already empty
      cart={};_serverCartItemIds={};
    }else{
      const{cart:newCart,ids:newIds}=_buildLocalFromServer(serverCart);
      cart=newCart;_serverCartItemIds=newIds;
    }
    updateBar();
  }catch(e){
    // Network failure — keep existing local state
  }
}

async function _syncAddToServer(productId,variantId,modifierOptionIds,notes,qty){
  // Blocking: returns server CartItem id or null on failure
  if(!restaurant||!_cartLocationId)return null;
  try{
    const body={product_id:productId,quantity:qty||1};
    if(variantId!=null)body.variant_id=variantId;
    if(modifierOptionIds&&modifierOptionIds.length)body.modifier_option_ids=modifierOptionIds;
    if(notes)body.notes=notes;
    const r=await fetch(`${API_BASE}/api/cart/items`,{
      method:'POST',
      headers:_cartHeaders(),
      body:JSON.stringify(body),
    });
    if(!r.ok){
      const e=await r.json().catch(()=>({}));
      const msg=typeof e.detail==='string'?e.detail:'Could not add item';
      if(typeof showToast==='function')showToast(msg);
      return null;
    }
    const serverCart=await r.json();
    const{cart:newCart,ids:newIds}=_buildLocalFromServer(serverCart);
    cart=newCart;_serverCartItemIds=newIds;
    return serverCart;
  }catch(e){
    if(typeof showToast==='function')showToast('Network error. Please try again.');
    return null;
  }
}

async function _syncRemoveFromServer(cartKey){
  const itemId=_serverCartItemIds[cartKey];
  if(!itemId||!restaurant||!_cartLocationId)return;
  try{
    const r=await fetch(`${API_BASE}/api/cart/items/${itemId}`,{
      method:'DELETE',
      headers:_cartHeaders(),
    });
    if(r.ok){
      const serverCart=await r.json();
      const{cart:newCart,ids:newIds}=_buildLocalFromServer(serverCart);
      cart=newCart;_serverCartItemIds=newIds;
    }
  }catch(e){/* network error — local already updated */}
}

async function _syncUpdateQtyOnServer(cartKey,newQty){
  const itemId=_serverCartItemIds[cartKey];
  if(!itemId||!restaurant||!_cartLocationId)return;
  try{
    const r=await fetch(`${API_BASE}/api/cart/items/${itemId}`,{
      method:'PATCH',
      headers:_cartHeaders(),
      body:JSON.stringify({quantity:newQty}),
    });
    if(!r.ok){
      // Rollback handled by caller
      return false;
    }
    return true;
  }catch(e){return false;}
}

// ── ADD TO CART ───────────────────────────────────────────────────────────────

// addLegacyItem — добавить legacy product (без вариантов) в корзину
// Phase 6: blocking server sync — awaits CartAPI response.
async function addLegacyItem(id,modifierOptionIds,notes){
  const p=findProduct(id);if(!p)return;
  const result=await _syncAddToServer(id,null,modifierOptionIds||[],notes||null,1);
  if(!result)return; // server rejected — local not updated
  refreshCtrl(id);updateBar();
  const card=document.getElementById('card-'+id);
  if(card){card.classList.remove('just-added');void card.offsetWidth;card.classList.add('just-added');}
}

// addVariantItem — добавить вариант продукта в корзину
// Phase 6: blocking server sync.
async function addVariantItem(productId,variant,modifierOptionIds,notes){
  const p=findProduct(productId);if(!p||!variant)return;
  const result=await _syncAddToServer(productId,variant.id,modifierOptionIds||[],notes||null,1);
  if(!result)return;
  refreshCtrl(productId);updateBar();
  const card=document.getElementById('card-'+productId);
  if(card){card.classList.remove('just-added');void card.offsetWidth;card.classList.add('just-added');}
}

// changeQty — принимает cartKey (строка), не числовой id
function changeQty(key,delta){
  if(!cart[key])return;
  cart[key].qty+=delta;
  if(cart[key].qty<=0)delete cart[key];
  const item=cart[key];
  const productId=item?item.id:(key.includes('__')?parseInt(key.split('__')[0]):parseInt(key));
  refreshCtrl(productId);
  updateBar();
  if(productModalCurrentId===productId)refreshModalCtrl();
}

// ── CART TOTALS ───────────────────────────────────────────────────────────────

function cartItems(){return Object.values(cart);}
function cartTotal(){return cartItems().reduce((s,i)=>s+i.price*i.qty,0);}
function cartCount(){return cartItems().reduce((s,i)=>s+i.qty,0);}

// ── CART BAR ──────────────────────────────────────────────────────────────────

function updateBar(){
  const n=cartCount();
  document.getElementById('cartCountBadge').textContent=n;
  document.getElementById('cartTotalBar').textContent=fmt(cartTotal());
  const bar=document.getElementById('cartBar');
  bar.classList.toggle('visible',n>0);
  const cap=bar.querySelector('.cart-cap');
  if(cap&&n>0){cap.classList.remove('pulse');void cap.offsetWidth;cap.classList.add('pulse');}
}

// ── OPEN / CLOSE CART ─────────────────────────────────────────────────────────

function openCart(){renderCart();document.getElementById('cartScreen').classList.add('active');}
function closeCart(){document.getElementById('cartScreen').classList.remove('active');}

// ── ORDER TYPE ────────────────────────────────────────────────────────────────

function selectType(t){
  orderType=t;
  document.getElementById('typeDelivery').classList.toggle('active',t==='delivery');
  document.getElementById('typeTakeaway').classList.toggle('active',t==='takeaway');
  document.getElementById('clientAddress').style.display=t==='delivery'?'block':'none';
  _updateDeliveryRow();
}

function _updateDeliveryRow(){
  const fee=restaurant?.delivery_fee||0;
  const min=restaurant?.min_order_amount||0;
  const feeRow=document.getElementById('deliveryFeeRow');
  const minRow=document.getElementById('minOrderRow');
  const total=cartTotal();

  if(feeRow){
    if(orderType==='delivery'&&fee>0){
      document.getElementById('deliveryFeeVal').textContent=fmt(fee);
      feeRow.style.display='flex';
    }else{
      feeRow.style.display='none';
    }
  }

  if(minRow){
    if(min>0&&total<min){
      document.getElementById('minOrderText').textContent=
        t('validation.minimum_order_hint',{amount:fmt(min),remaining:fmt(min-total)});
      minRow.style.display='flex';
    }else{
      minRow.style.display='none';
    }
  }

  const grandTotal=total+(orderType==='delivery'?fee:0);
  const el=document.getElementById('summaryTotal');
  if(el)el.textContent=fmt(grandTotal);
}

// ── RENDER CART ───────────────────────────────────────────────────────────────

function renderCart(){
  const list=document.getElementById('cartItemsList');list.innerHTML='';
  Object.entries(cart).forEach(([cartKey,item])=>{
    const div=document.createElement('div');div.className='cart-item';

    if(item.photo){
      const phWrap=document.createElement('div');phWrap.className='ci-photo';
      const img=document.createElement('img');
      img.src=item.photo;img.alt=item.name;img.loading='lazy';
      phWrap.appendChild(img);div.appendChild(phWrap);
    }else{
      const phWrap=document.createElement('div');phWrap.className='ci-ph';
      phWrap.innerHTML='<svg class="icon" viewBox="0 0 24 24"><path d="M18 8a4 4 0 0 1 0 8M6 4v16M6 4c0 2.5 2.5 2.5 2.5 5S6 11.5 6 14"/></svg>';
      div.appendChild(phWrap);
    }

    const info=document.createElement('div');info.className='ci-info';
    const nameEl=document.createElement('div');nameEl.className='ci-name';
    nameEl.textContent=item.name;
    info.appendChild(nameEl);
    if(item.variant_name){
      const vnEl=document.createElement('div');
      vnEl.style.cssText='font-size:11.5px;color:var(--ink4);margin-top:1px;font-style:italic;';
      vnEl.textContent=item.variant_name;
      info.appendChild(vnEl);
    }
    const priceEl=document.createElement('div');priceEl.className='ci-price';
    priceEl.textContent=item.qty+' × '+fmt(item.price);
    info.appendChild(priceEl);
    div.appendChild(info);

    const safeKey=cartKey.replace(/'/g,"\\'");
    const qty=document.createElement('div');qty.className='qty-unit';
    qty.innerHTML='<button class="qty-ring" onclick="cartChangeQty(\''+safeKey+'\',-1)"><svg class="icon" viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button><span class="qty-digit">'+item.qty+'</span><button class="qty-ring" onclick="cartChangeQty(\''+safeKey+'\',1)"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>';
    div.appendChild(qty);

    list.appendChild(div);
  });
  document.getElementById('summaryCount').textContent=t('cart.item_count',{n:cartCount()});
  document.getElementById('summarySubtotal').textContent=fmt(cartTotal());
  document.getElementById('summaryTotal').textContent=fmt(cartTotal());
  _updateDeliveryRow();
  if(orderType==='dine_in'){
    document.getElementById('orderTypeSection').style.display='none';
    document.getElementById('clientAddress').style.display='none';
    document.getElementById('clientName').style.display='none';
    document.getElementById('clientPhone').style.display='none';
    document.getElementById('tableBanner').style.display='flex';
    document.getElementById('tableBannerTitle').textContent=t('cart.table_label',{n:tableNumber});
  }else{
    document.getElementById('orderTypeSection').style.display='block';
    document.getElementById('tableBanner').style.display='none';
    document.getElementById('clientName').style.display='block';
    document.getElementById('clientPhone').style.display='block';
    document.getElementById('clientAddress').style.display=orderType==='delivery'?'block':'none';
  }
}

// cartChangeQty — принимает cartKey (строка), вызывается из renderCart
function cartChangeQty(cartKey,delta){
  if(!cart[cartKey])return;
  cart[cartKey].qty+=delta;
  if(cart[cartKey].qty<=0){
    const item=cart[cartKey];
    delete cart[cartKey];
    if(item)refreshCtrl(item.id);
  }else{
    const item=cart[cartKey];
    if(item)refreshCtrl(item.id);
  }
  updateBar();
  if(cartCount()===0){closeCart();return;}
  renderCart();
}
