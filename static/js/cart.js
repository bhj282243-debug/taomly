// ── CART.JS — Cart logic & UI (Phase 6) ──────────────────────────────────────
// Содержит: getCartKey, server sync helpers, addLegacyItem, addVariantItem,
//           changeQty, cartChangeQty, renderCart, openCart, closeCart,
//           updateBar, cartItems, cartTotal, cartCount

// ── CART KEY ──────────────────────────────────────────────────────────────────
// Legacy product:  "123"
// Variant product: "123__456"

function getCartKey(productId,variantId){
  return variantId!=null?String(productId)+'__'+String(variantId):String(productId);
}

// ── SERVER CART SYNC (Phase 6) ────────────────────────────────────────────────
// Server Cart is authoritative. Local cart={} = optimistic UI cache.
// add/remove : blocking  — await server response before updating local state.
// qty update : optimistic — update local first, rollback on server error.
// page load  : hydrate from GET /api/cart.

// Map: cartKey → server CartItem id (needed for PATCH/DELETE /api/cart/items/{id})
let _serverCartItemIds={};

function _buildLocalFromServer(serverCart){
  // Rebuild local cart={} and _serverCartItemIds from CartResponse.
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
      photo:null,
      _server_item_id:item.id,
    };
    newIds[key]=item.id;
    // Restore photo from local product data if available
    try{
      const localProd=typeof findProduct==='function'?findProduct(item.product_id):null;
      if(localProd)newCart[key].photo=localProd.photo_url||null;
    }catch(e){}
  });
  return{cart:newCart,ids:newIds};
}

async function syncCartFromServer(){
  // Called on page load — hydrates local cart from server authoritative state.
  if(!restaurant||!_cartLocationId)return;
  try{
    const r=await fetch(`${API_BASE}/api/cart`,{
      method:'GET',
      headers:_cartHeaders(),
    });
    if(!r.ok)return;
    const serverCart=await r.json();
    if(serverCart.cart_id===0){
      cart={};_serverCartItemIds={};
    }else{
      const{cart:c,ids:i}=_buildLocalFromServer(serverCart);
      cart=c;_serverCartItemIds=i;
    }
    updateBar();
  }catch(e){
    // Network failure on load — keep existing local state (empty)
  }
}

async function _syncAddToServer(productId,variantId,modifierOptionIds,notes,qty){
  // Blocking: returns server CartResponse or null on failure.
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
      let msg='Could not add item';
      try{const e=await r.json();if(typeof e.detail==='string')msg=e.detail;}catch(_){}
      if(typeof showToast==='function')showToast(msg);
      return null;
    }
    const serverCart=await r.json();
    const{cart:c,ids:i}=_buildLocalFromServer(serverCart);
    cart=c;_serverCartItemIds=i;
    return serverCart;
  }catch(e){
    if(typeof showToast==='function')showToast('Network error. Please try again.');
    return null;
  }
}

async function _syncRemoveFromServer(cartKey){
  const itemId=_serverCartItemIds[cartKey];
  if(!itemId||!restaurant||!_cartLocationId)return;
  delete _serverCartItemIds[cartKey];
  try{
    await fetch(`${API_BASE}/api/cart/items/${itemId}`,{
      method:'DELETE',
      headers:_cartHeaders(),
    });
    // Server is authoritative — local already updated optimistically
  }catch(e){}
}

async function _syncUpdateQtyOnServer(cartKey,newQty){
  const itemId=_serverCartItemIds[cartKey];
  if(!itemId||!restaurant||!_cartLocationId)return true; // no server — accept local
  try{
    const r=await fetch(`${API_BASE}/api/cart/items/${itemId}`,{
      method:'PATCH',
      headers:_cartHeaders(),
      body:JSON.stringify({quantity:newQty}),
    });
    return r.ok;
  }catch(e){return false;}
}

// ── ADD ITEM — blocking server sync ───────────────────────────────────────────

async function addLegacyItem(id,modifierOptionIds,notes){
  // Phase 6: await server response. Local state updated from server CartResponse.
  const p=findProduct(id);if(!p)return;
  const result=await _syncAddToServer(id,null,modifierOptionIds||[],notes||null,1);
  if(!result)return; // server rejected — local not updated
  refreshCtrl(id);
  updateBar();
  if(productModalCurrentId===id)refreshModalCtrl();
  const card=document.getElementById('card-'+id);
  if(card){card.classList.remove('just-added');void card.offsetWidth;card.classList.add('just-added');}
}

async function addVariantItem(productId,variant,modifierOptionIds,notes){
  // Phase 6: await server response.
  const p=findProduct(productId);if(!p||!variant)return;
  const result=await _syncAddToServer(productId,variant.id,modifierOptionIds||[],notes||null,1);
  if(!result)return;
  refreshCtrl(productId);
  updateBar();
  if(productModalCurrentId===productId)refreshModalCtrl();
  const card=document.getElementById('card-'+productId);
  if(card){card.classList.remove('just-added');void card.offsetWidth;card.classList.add('just-added');}
}

// ── CHANGE QTY — called from product cards (not cart drawer) ─────────────────

function changeQty(key,delta){
  // Legacy sync helper for product card +/- buttons outside cart drawer.
  // Phase 6: delegates to cartChangeQty for consistent async handling.
  cartChangeQty(key,delta);
}

// ── CART TOTALS ───────────────────────────────────────────────────────────────

function cartItems(){return Object.values(cart);}
function cartTotal(){return cartItems().reduce((s,i)=>s+i.price*i.qty,0);}
function cartCount(){return cartItems().reduce((s,i)=>s+i.qty,0);}

// ── CART CHANGE QTY — optimistic update + server rollback ────────────────────

async function cartChangeQty(cartKey,delta){
  if(!cart[cartKey])return;

  const prevQty=cart[cartKey].qty;
  const newQty=prevQty+delta;

  if(newQty<=0){
    // Remove item — optimistic local delete, then async server delete
    const item=cart[cartKey];
    delete cart[cartKey];
    delete _serverCartItemIds[cartKey];
    if(item)refreshCtrl(item.id);
    updateBar();
    if(cartCount()===0){closeCart();}else{renderCart();}
    // Fire-and-forget remove is acceptable: worst case = ghost item on server.
    // Phase 7 checkout will validate and reconcile.
    await _syncRemoveFromServer(cartKey);
  }else{
    // Optimistic update
    cart[cartKey].qty=newQty;
    if(cart[cartKey])refreshCtrl(cart[cartKey].id);
    updateBar();
    renderCart();
    // Async confirm with rollback on failure
    const ok=await _syncUpdateQtyOnServer(cartKey,newQty);
    if(!ok&&cart[cartKey]){
      // Rollback to previous quantity
      cart[cartKey].qty=prevQty;
      if(cart[cartKey])refreshCtrl(cart[cartKey].id);
      updateBar();
      renderCart();
      if(typeof showToast==='function')showToast('Could not update quantity. Please try again.');
    }
  }
}

// ── UPDATE BAR ────────────────────────────────────────────────────────────────

function updateBar(){
  const n=cartCount(),total=cartTotal();
  const bar=document.getElementById('cartBar');
  if(!bar)return;
  if(n===0){bar.style.display='none';return;}
  bar.style.display='flex';
  const cnt=document.getElementById('cartCount');
  const pr=document.getElementById('cartPrice');
  if(cnt)cnt.textContent=n;
  if(pr)pr.textContent=typeof formatPrice==='function'?formatPrice(total,restaurant):total;
}

// ── REFRESH CTRL — update +/- buttons on product card ────────────────────────

function refreshCtrl(productId){
  const key=getCartKey(productId,null);
  // Check both legacy key and any variant key
  let qty=0;
  Object.keys(cart).forEach(k=>{
    const pid=k.includes('__')?parseInt(k.split('__')[0]):parseInt(k);
    if(pid===productId)qty+=cart[k].qty;
  });
  const ctrl=document.getElementById('ctrl-'+productId);
  const addBtn=document.getElementById('add-'+productId);
  if(!ctrl&&!addBtn)return;
  if(qty===0){
    if(ctrl)ctrl.style.display='none';
    if(addBtn)addBtn.style.display='';
  }else{
    if(ctrl){ctrl.style.display='flex';const d=ctrl.querySelector('.qty-digit');if(d)d.textContent=qty;}
    if(addBtn)addBtn.style.display='none';
  }
}

// ── OPEN / CLOSE CART ─────────────────────────────────────────────────────────

function openCart(){
  renderCart();
  const d=document.getElementById('cartDrawer');if(d)d.classList.add('open');
  const ov=document.getElementById('cartOverlay');if(ov)ov.style.display='block';
}

function closeCart(){
  const d=document.getElementById('cartDrawer');if(d)d.classList.remove('open');
  const ov=document.getElementById('cartOverlay');if(ov)ov.style.display='none';
}

// ── RENDER CART ───────────────────────────────────────────────────────────────

function renderCart(){
  const list=document.getElementById('cartList');
  const total=document.getElementById('cartTotal');
  if(!list)return;

  const items=cartItems();
  if(items.length===0){list.innerHTML='<p class="cart-empty">'+((typeof t==='function'?t('cart.empty'):'')||'Корзина пуста')+'</p>';if(total)total.textContent='';return;}

  list.innerHTML=items.map(item=>{
    const key=getCartKey(item.id,item.variant_id);
    const safeKey=key.replace(/'/g,"\\'");
    const photo=item.photo?`<img src="${esc(item.photo)}" alt="">`:
      `<div class="ci-ph">${esc((item.name||'?')[0].toUpperCase())}</div>`;
    const sub=item.variant_name?`<span class="ci-sub">${esc(item.variant_name)}</span>`:'';
    const price=typeof formatPrice==='function'?formatPrice(item.price*item.qty,restaurant):(item.price*item.qty);
    return `<div class="cart-item">
      <div class="ci-img">${photo}</div>
      <div class="ci-info">
        <span class="ci-name">${esc(item.name)}</span>${sub}
        <span class="ci-price">${price}</span>
      </div>
      <div class="ci-qty">
        <button class="qty-ring" onclick="cartChangeQty('${safeKey}',-1)"><svg class="icon" viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
        <span class="qty-digit">${item.qty}</span>
        <button class="qty-ring" onclick="cartChangeQty('${safeKey}',1)"><svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button>
      </div>
    </div>`;
  }).join('');

  if(total){
    const t_val=cartTotal();
    total.textContent=typeof formatPrice==='function'?formatPrice(t_val,restaurant):t_val;
  }
}
