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

// ── ADD TO CART ───────────────────────────────────────────────────────────────

// addLegacyItem — добавить legacy product (без вариантов) в корзину
function addLegacyItem(id){
  const p=findProduct(id);if(!p)return;
  const key=getCartKey(id,null);
  if(cart[key]){cart[key].qty+=1;}
  else{cart[key]={id,variant_id:null,name:p.name,variant_name:null,price:p.price,qty:1,photo:p.photo_url||null};}
  refreshCtrl(id);updateBar();
  const card=document.getElementById('card-'+id);
  if(card){card.classList.remove('just-added');void card.offsetWidth;card.classList.add('just-added');}
}

// addVariantItem — добавить вариант продукта в корзину
function addVariantItem(productId,variant){
  const p=findProduct(productId);if(!p||!variant)return;
  const key=getCartKey(productId,variant.id);
  if(cart[key]){cart[key].qty+=1;}
  else{cart[key]={id:productId,variant_id:variant.id,name:p.name,variant_name:variant.name,price:variant.price,qty:1,photo:p.photo_url||null};}
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
