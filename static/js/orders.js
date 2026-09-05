// ── ORDERS.JS — Order submission & status (R-3.4B) ───────────────────────────
// Зависит от: state.js, utils.js, i18n.js, cart.js (cartItems, cartTotal,
//             updateBar), menu.js (renderMenu)
// Содержит: submitOrder, showSuccess, backToMenu, showToast,
//           _renderStatusSteps, _getStepsForType, _mapStatus, _getStatusLabels,
//           _stopPolling, _pollOrderStatus

// ── ORDER STATUS STEPS ────────────────────────────────────────────────────────

const STATUS_STEPS_DELIVERY  = ['new','preparing','delivering','completed'];
const STATUS_STEPS_TAKEAWAY  = ['new','preparing','completed'];
const STATUS_STEPS_DINE      = ['new','preparing','completed'];

// STATUS_LABELS — пересоздаётся после loadI18n() чтобы использовать текущий язык
function _getStatusLabels(){
  return {
    new:        t('order.status_new'),
    preparing:  t('order.preparing'),
    delivering: t('order.delivering'),
    completed:  t('order.ready'),
    cancelled:  t('order.cancelled'),
  };
}

function _getStepsForType(type){
  if(type==='delivery')return STATUS_STEPS_DELIVERY;
  if(type==='takeaway')return STATUS_STEPS_TAKEAWAY;
  return STATUS_STEPS_DINE;
}

function _mapStatus(s,type){
  // Промежуточные статусы → ближайший видимый шаг
  if(s==='accepted')return'preparing';
  if(s==='ready_for_delivery')return type==='delivery'?'delivering':'completed';
  if(s==='delivering')return type==='delivery'?'delivering':'completed';
  return s;
}

function _renderStatusSteps(currentStatus,type){
  const block=document.getElementById('orderStatusBlock');
  const container=document.getElementById('statusSteps');
  if(!block||!container)return;
  if(currentStatus==='cancelled'){
    container.innerHTML=`<div class="status-cancelled">${t('order.status_cancelled')}</div>`;
    block.style.display='block';
    return;
  }
  const steps=_getStepsForType(type);
  const currentIdx=steps.indexOf(_mapStatus(currentStatus,type));
  const labels=_getStatusLabels();
  let html='';
  steps.forEach((s,i)=>{
    let cls='status-step';
    if(i<currentIdx)cls+=' done';
    else if(i===currentIdx)cls+=' active';
    const check=`<svg class="step-check" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>`;
    html+=`<div class="${cls}"><div class="step-dot"></div><span class="step-label">${labels[s]||s}</span>${check}</div>`;
  });
  container.innerHTML=html;
  block.style.display='block';
}

// ── POLLING ───────────────────────────────────────────────────────────────────

function _stopPolling(){
  if(_pollingTimer){clearInterval(_pollingTimer);_pollingTimer=null;}
}

async function _pollOrderStatus(){
  if(!_pollingOrderId||!_pollingHeaders)return;
  try{
    const res=await fetch(`${API_BASE}/api/orders/my/${_pollingOrderId}`,{headers:_pollingHeaders});
    if(!res.ok)return;
    const order=await res.json();
    _renderStatusSteps(order.status,orderType);
    if(order.status==='completed'||order.status==='cancelled')_stopPolling();
  }catch(e){/* молча, не прерываем UI */}
}

// ── SUBMIT ORDER ──────────────────────────────────────────────────────────────

async function submitOrder(){
  const btn=document.getElementById('checkoutBtn');
  const name=document.getElementById('clientName').value.trim();
  const phone=document.getElementById('clientPhone').value.trim();
  const address=document.getElementById('clientAddress').value.trim();
  const comment=document.getElementById('clientComment').value.trim();
  if(orderType!=='dine_in'){if(!name){showToast(t('validation.name_required'));return;}if(!phone||phone==='+998'){showToast(t('validation.phone_required'));return;}}
  if(orderType==='delivery'&&!address){showToast(t('validation.address_required'));return;}
  const minAmt=restaurant?.min_order_amount||0;
  if(minAmt>0&&cartTotal()<minAmt){
    showToast(t('validation.minimum_order',{amount:fmt(minAmt)}));return;
  }
  const tg=window.Telegram?.WebApp;
  const initData=tg?.initData;
  btn.disabled=true;btn.textContent=t('cart.submitting');
  const payload={client_name:name||null,client_phone:orderType==='dine_in'?null:(phone||null),order_type:orderType,address:orderType==='delivery'?address:null,table_id:orderType==='dine_in'?tableDbId:null,comment:comment||null,items:cartItems().map(i=>{const it={product_id:i.id,quantity:i.qty};if(i.variant_id!=null)it.variant_id=i.variant_id;return it;})};
  try{
    const headers={'Content-Type':'application/json','X-Restaurant-Id':String(restaurant.id)};
    if(initData)headers['X-Telegram-Init-Data']=initData;
    const res=await fetch(`${API_BASE}/api/orders/`,{method:'POST',headers,body:JSON.stringify(payload)});
    if(!res.ok){const e=await res.json();const msg=Array.isArray(e.detail)?e.detail.map(x=>x.msg||JSON.stringify(x)).join(', '):(typeof e.detail==='string'?e.detail:JSON.stringify(e.detail)||t('common.error'));throw new Error(msg);}
    const order=await res.json();showSuccess(order.id);
  }catch(e){showToast(t('error.generic',{message:e.message}));btn.disabled=false;btn.textContent=t('cart.submit');}
}

// ── SUCCESS SCREEN ────────────────────────────────────────────────────────────

function showSuccess(orderId){
  document.getElementById('successOrderId').textContent='#'+String(orderId).padStart(4,'0');
  const msgs={dine_in:t('success.dine_in'),takeaway:t('success.takeaway'),delivery:t('success.delivery')};
  document.getElementById('successSubtitle').textContent=msgs[orderType]||msgs.delivery;
  document.getElementById('cartScreen').classList.remove('active');
  document.getElementById('successScreen').classList.add('active');
  cart={};updateBar();
  _renderStatusSteps('new',orderType);
  const tg=window.Telegram?.WebApp;
  const initData=tg?.initData||'';
  if(initData&&restaurant){
    _stopPolling();
    _pollingOrderId=orderId;
    _pollingHeaders={'X-Restaurant-Id':String(restaurant.id),'X-Telegram-Init-Data':initData};
    _pollingTimer=setInterval(_pollOrderStatus,5000);
  }
}

function backToMenu(){
  _stopPolling();
  document.getElementById('successScreen').classList.remove('active');
  const block=document.getElementById('orderStatusBlock');
  if(block)block.style.display='none';
  renderMenu();
}

// ── TOAST ─────────────────────────────────────────────────────────────────────

function showToast(msg){
  let t=document.getElementById('_toast');
  if(!t){
    t=document.createElement('div');t.id='_toast';
    t.style.cssText='position:fixed;bottom:116px;left:50%;transform:translateX(-50%) translateY(14px);z-index:9999;background:rgba(24,20,15,0.92);border-radius:100px;padding:13px 24px;font-size:13px;color:#FAF8F3;white-space:nowrap;opacity:0;transition:opacity .22s ease,transform .22s ease;pointer-events:none;letter-spacing:.2px;';
    document.body.appendChild(t);
  }
  t.textContent=msg;t.style.opacity='1';t.style.transform='translateX(-50%) translateY(0)';
  if(_tt)clearTimeout(_tt);
  _tt=setTimeout(()=>{t.style.opacity='0';t.style.transform='translateX(-50%) translateY(14px)';},2800);
}
