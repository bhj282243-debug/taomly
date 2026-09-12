"""
modules/payments/router.py — Taomly Platform
Phase 8: Payment API endpoints.

Endpoints:
  POST /api/payments                    — Customer creates payment, gets checkout URL
  GET  /api/payments/{payment_id}       — Customer checks payment status
  POST /api/payments/payme              — Payme JSON-RPC 2.0 callback (always HTTP 200)
  POST /api/payments/click              — Click SHOP API callback (always HTTP 200)
  POST /api/payments/sandbox/callback   — TestSandboxProvider callback (test only)

Security rules:
  - /api/payments: requires get_telegram_user auth. restaurant_id from auth, not body.
  - /api/payments/payme: authenticated via Basic Auth inside service.
  - /api/payments/click: authenticated via MD5 sign_string inside service.
  - /api/payments/sandbox/callback: only available when ENVIRONMENT != 'production'.
  - Client CANNOT set status=paid. Only verified provider callbacks can.
  - Provider callbacks ALWAYS return HTTP 200 to prevent provider retry storms.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from auth import get_telegram_user, TelegramUser
from config import settings
from database import get_db

from .schemas import PaymentCreateRequest, PaymentResponse, PaymentStatusResponse
from .service import create_payment, process_payme_callback, process_click_callback
from modules.payments.providers import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/payments — Create payment (Customer)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment_endpoint(
    data: PaymentCreateRequest,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """
    Initiate payment for an Order.

    Security:
      - restaurant_id is taken from verified TelegramUser, NOT from request body.
      - amount and currency are taken from Order, NOT from request body.
      - Client cannot supply amount, currency, or status.
      - provider='sandbox' is only allowed in non-production environments.

    Returns payment_id + checkout_url (redirect customer to checkout_url).
    """
    # Block sandbox in production
    if data.provider == "sandbox" and settings.ENVIRONMENT == "production":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Sandbox provider is not available in production.",
        )

    # restaurant_id is authoritative from TelegramUser — never from client body
    restaurant_id = tg_user.restaurant_id

    payment, checkout_url = create_payment(
        db=db,
        order_id=data.order_id,
        provider=data.provider,
        restaurant_id=restaurant_id,
        idempotency_key=data.idempotency_key,
    )

    return PaymentResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        restaurant_id=payment.restaurant_id,
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        provider=payment.provider,
        checkout_url=checkout_url,
        idempotency_key=payment.idempotency_key,
        created_at=payment.created_at,
        paid_at=payment.paid_at,
    )


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/payments/{payment_id} — Status check (Customer)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/{payment_id}", response_model=PaymentStatusResponse)
def get_payment_status(
    payment_id: int,
    tg_user: TelegramUser = Depends(get_telegram_user),
    db: Session = Depends(get_db),
):
    """Check payment status. Tenant-isolated: customer can only see their restaurant's payments."""
    from models.payments import Payment as PaymentModel

    payment = (
        db.query(PaymentModel)
        .filter(
            PaymentModel.id == payment_id,
            PaymentModel.restaurant_id == tg_user.restaurant_id,
        )
        .first()
    )
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    return PaymentStatusResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        provider=payment.provider,
        paid_at=payment.paid_at,
    )


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/payments/payme — Payme JSON-RPC 2.0 callback
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/payme")
async def payme_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Payme Merchant API callback endpoint.

    Protocol: JSON-RPC 2.0. Always returns HTTP 200.
    Authentication: Basic Auth (merchant_id:merchant_key).
    Tenant is resolved from merchant_id → RestaurantPaymentConfig.

    Never raises HTTP 4xx/5xx — Payme expects HTTP 200 with JSON-RPC error field.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Payme callback: JSON parse error")
        return JSONResponse(
            status_code=200,
            content={"error": {"code": -32700, "message": {"en": "Parse error"}}},
        )

    # Normalise headers to lowercase for consistent access
    headers = {k.lower(): v for k, v in request.headers.items()}

    logger.info(
        "Payme callback: method=%s", body.get("method", "unknown")
    )

    result = process_payme_callback(
        db=db,
        request_headers=headers,
        request_body=body,
    )

    return JSONResponse(status_code=200, content=result)


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/payments/click — Click SHOP API callback
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/click")
async def click_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Click SHOP API callback endpoint (Prepare + Complete).

    Protocol: HTTP POST form-like JSON. Always returns HTTP 200.
    Authentication: MD5 sign_string in body.
    Tenant is resolved from service_id → RestaurantPaymentConfig.

    action=0 → Prepare (PENDING → PROCESSING)
    action=1 → Complete (PROCESSING → PAID or FAILED)
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Click callback: JSON parse error")
        return JSONResponse(
            status_code=200,
            content={"error": -8, "error_note": "Parse error"},
        )

    logger.info(
        "Click callback: action=%s service_id=%s",
        body.get("action"), body.get("service_id"),
    )

    result = process_click_callback(db=db, request_body=body)

    return JSONResponse(status_code=200, content=result)


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/payments/sandbox/callback — TestSandboxProvider (test env only)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/sandbox/callback")
async def sandbox_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    TestSandboxProvider callback for automated CI tests.

    BLOCKED in production. Only available when ENVIRONMENT != 'production'.

    Body: {"payment_id": <int>, "action": "pay"|"fail"|"cancel", "amount": <int>}
    """
    if settings.ENVIRONMENT == "production":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=200,
            content={"error": -1, "message": "Parse error"},
        )

    from .service import _click_complete, _click_prepare
    from .providers.sandbox import TestSandboxProvider
    from models.payments import Payment as PaymentModel

    adapter = TestSandboxProvider()
    try:
        callback = adapter.parse_callback(body)
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={"error": -1, "message": str(exc)},
        )

    # Sandbox bypasses the tenant check for test simplicity —
    # restaurant_id is determined from the Payment record itself.
    payment_id_str = callback.payment_ref
    try:
        payment_id = int(payment_id_str)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=200,
            content={"error": -5, "message": "Invalid payment_id"},
        )

    payment = db.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if payment is None:
        return JSONResponse(
            status_code=200,
            content={"error": -5, "message": "Payment not found"},
        )

    # Delegate to Click-like Complete logic with sandbox adapter
    result = process_click_callback.__wrapped__ if hasattr(process_click_callback, "__wrapped__") else None

    # Direct state transition for sandbox
    from .service import _click_complete as _cc
    # Build a fake Click-style body to reuse _click_complete
    fake_click_body = {
        "service_id": "sandbox",
        "click_trans_id": f"sandbox-{payment_id}",
        "merchant_trans_id": str(payment_id),
        "merchant_prepare_id": payment_id,
        "amount": payment.amount / 100,  # tiyins → soums (will be reconverted)
        "action": "1",
        "sign_time": "20240101 120000",
        "sign_string": "sandbox-bypasses-sign",
        "error": 0 if body.get("action") == "pay" else -1,
    }

    # For sandbox we apply transitions directly, bypassing Click sign check
    from datetime import datetime, timezone as _tz
    from models.orders import Order as _Order

    now = datetime.now(_tz.utc)

    # Reload with lock
    payment = (
        db.query(PaymentModel)
        .filter(PaymentModel.id == payment_id)
        .with_for_update()
        .first()
    )
    if payment is None:
        db.rollback()
        return JSONResponse(status_code=200, content={"error": -5, "message": "Not found"})

    action = body.get("action", "pay")
    if payment.status == "paid":
        db.rollback()
        return JSONResponse(status_code=200, content={"sandbox": True, "status": "already_paid"})

    if action == "pay":
        if payment.status == "pending":
            payment.status = "processing"
            payment.provider_transaction_id = f"sandbox-tx-{payment_id}"
        if payment.status == "processing":
            payment.status = "paid"
            payment.paid_at = now
            payment.updated_at = now
            order = db.query(_Order).filter(_Order.id == payment.order_id).first()
            if order:
                order.paid_at = now
    elif action == "fail":
        payment.status = "failed"
        payment.failure_reason = "sandbox_failure"
        payment.updated_at = now
    elif action == "cancel":
        if payment.status in ("pending", "processing"):
            payment.status = "cancelled"
            payment.updated_at = now

    try:
        db.commit()
    except Exception:
        db.rollback()
        return JSONResponse(status_code=200, content={"error": -8, "message": "DB error"})

    db.refresh(payment)
    return JSONResponse(
        status_code=200,
        content={
            "sandbox": True,
            "payment_id": payment.id,
            "status": payment.status,
        },
    )
