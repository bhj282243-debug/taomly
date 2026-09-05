// ── STATE.JS — Global application state (R-3.2) ──────────────────────────────
// Все глобальные переменные приложения. Загружается первым.
// Не переименовывать — inline onclick и остальной JS зависят от этих имён.

const API_BASE='';

let restaurant=null;
let cart={};
let orderType='delivery';
let tableNumber=null;
let tableDbId=null;
let restaurantSlug=null;

// Menu rendering
let _ai=0;

// Modal state
let productModalCurrentId=null;
let _vpSelectedVariantId=null;

// Order polling
let _pollingTimer=null;
let _pollingOrderId=null;
let _pollingHeaders=null;

// Toast timer
let _tt=null;

// PWA install prompt
let _dip=null;
