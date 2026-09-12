"""
modules/payments/service.py — Taomly Platform
Phase 8: Payment Service — business invariants, state machine, DB transactions.

Responsibilities of this layer:
  - Amount/currency/ownership verification (from Order, never from client).
  - Active Payment invariant enforcement.
  - Idempotency key semantics.
  - PostgreSQL locking (SELECT FOR UPDATE — not NOWAIT for callbacks).
  - Payment state machine transitions.
  - Order.paid_at projection.
  - Merchant config resolution and tenant isolation.

What this layer does NOT do:
  - Provider-specific protocol parsing (delegated to adapter).
  - Checkout URL format (delegated to adapter).
  - Client cannot trigger PAID from here — only verified provider callback.

State machine:
  PENDING → PROCESSING → PAID (terminal)
  PENDING → FAILED
  PENDING → CANCELLED
  PROCESSING → FAILED

Transaction rule:
  Payment creation: DB transaction wraps INSERT only. No external HTTP inside TX.
  Provider callback: authenticate/parse BEFORE TX. TX wraps only DB mutations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import decrypt_token
from models.orders import Order
from models.payments import Payment, PaymentAttempt, RestaurantPaymentConfig

from .providers.base import (
    CALLBACK_METHOD_CANCEL,
    CALLBACK_METHOD_CHECK_PERFORM,
    CALLBACK_METHOD_CHECK_STATUS,
    CALLBACK_METHOD_COMPLETE,
    CALLBACK_METHOD_CREATE,
    CALLBACK_METHOD_PERFORM,
    CALLBACK_METHOD_PREPARE,
    ParsedCallback,
    ProviderCallbackError,
)
from .providers import get_provider, ALLOWED_PROVIDERS

logger = logging.getLogger(__name__)

# ── Payment state machine ──────────────────────────────────────────────────────
# active = may block new Payment creation for same Order
_ACTIVE_STATUSES = {"pending", "processing"}

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":    {"processing", "failed", "cancelled"},
    "processing": {"paid", "failed"},
    "paid":       set(),   # terminal
    "failed":     set(),   # terminal
    "cancelled":  set(),   # terminal
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_ms() -> int:
    """Current UTC timestamp in milliseconds (for Payme responses)."""
    return int(_now_utc().timestamp() * 1000)


# ──────────────────────────────────────────────────────────────────────────────
# MERCHANT CONFIG HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_config_by_merchant_id(
    db: Session,
    merchant_id: str,
    provider: str,
) -> RestaurantPaymentConfig:
    """
    Resolve RestaurantPaymentConfig for Payme by Basic Auth username (merchant_id).
    Raises HTTP 401 (presented to Payme as -32504) if not found.
    Used by: PaymeProvider callback routing.
    """
    config = (
        db.query(RestaurantPaymentConfig)
        .filter(
            RestaurantPaymentConfig.merchant_id == merchant_id,
            RestaurantPaymentConfig.provider == provider,
            RestaurantPaymentConfig.is_active.is_(True),
        )
        .first()
    )
    if config is None:
        raise ProviderCallbackError(-32504, "Merchant config not found")
    return config


def _resolve_config_by_service_id(
    db: Session,
    service_id: str,
    provider: str,
) -> RestaurantPaymentConfig:
    """
    Resolve RestaurantPaymentConfig for Click by service_id.
    Used by: ClickProvider callback routing.
    """
    config = (
        db.query(RestaurantPaymentConfig)
        .filter(
            RestaurantPaymentConfig.service_id == service_id,
            RestaurantPaymentConfig.provider == provider,
            RestaurantPaymentConfig.is_active.is_(True),
        )
        .first()
    )
    if config is None:
        raise ProviderCallbackError(-1, "Merchant config not found for service_id")
    return config


def _get_config_for_restaurant(
    db: Session,
    restaurant_id: int,
    provider: str,
) -> RestaurantPaymentConfig:
    """
    Load RestaurantPaymentConfig for Payment creation.
    Called by create_payment() — restaurant_id comes from Order (authoritative).
    """
    config = (
        db.query(RestaurantPaymentConfig)
        .filter(
            RestaurantPaymentConfig.restaurant_id == restaurant_id,
            RestaurantPaymentConfig.provider == provider,
            RestaurantPaymentConfig.is_active.is_(True),
        )
        .first()
    )
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No active payment configuration for provider '{provider}' "
                f"for this restaurant. Contact the restaurant."
            ),
        )
    return config


# ──────────────────────────────────────────────────────────────────────────────
# PAYMENT CREATION
# ──────────────────────────────────────────────────────────────────────────────

def create_payment(
    db: Session,
    order_id: int,
    provider: str,
    restaurant_id: int,       # from TelegramUser.restaurant_id — NOT from client body
    idempotency_key: Optional[str],
) -> tuple[Payment, str]:
    """
    Create a new Payment for an Order and return (payment, checkout_url).

    Security contract:
      - restaurant_id comes from verified TelegramUser, never from request body.
      - amount is taken from Order.total_amount (server-authoritative).
      - currency is taken from Order.currency (server-authoritative).
      - Client cannot set status, amount, currency, restaurant_id.
      - External HTTP calls happen OUTSIDE this function (checkout URL is local).

    Returns (Payment, checkout_url).
    Raises HTTPException on any business rule violation.
    """
    # ── Validate provider ──────────────────────────────────────────────────
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown provider '{provider}'. Allowed: {sorted(ALLOWED_PROVIDERS)}",
        )

    # ── Load Order — source of truth for amount/currency/ownership ─────────
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.restaurant_id == restaurant_id,
        )
        .first()
    )
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # ── Verify Order is payable ────────────────────────────────────────────
    # Orders created by Phase 7 checkout start as 'accepted'. Payment is valid
    # for any non-terminal, non-cancelled order status.
    if order.status in ("completed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order status '{order.status}' is not payable.",
        )

    # ── Idempotency: same (order_id, idempotency_key) → same Payment ───────
    if idempotency_key is not None:
        existing = (
            db.query(Payment)
            .filter(
                Payment.order_id == order_id,
                Payment.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            # Replay: return existing payment with regenerated checkout_url
            config = _get_config_for_restaurant(db, restaurant_id, provider)
            checkout_url = _generate_checkout_url(existing, config)
            return existing, checkout_url

    # ── Check already PAID ─────────────────────────────────────────────────
    paid_payment = (
        db.query(Payment)
        .filter(
            Payment.order_id == order_id,
            Payment.status == "paid",
        )
        .first()
    )
    if paid_payment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This order has already been paid.",
        )

    # ── Active Payment invariant (service-level check before DB insert) ────
    # DB partial unique index is the final guard; this gives a better error msg.
    active_payment = (
        db.query(Payment)
        .filter(
            Payment.order_id == order_id,
            Payment.status.in_(list(_ACTIVE_STATUSES)),
        )
        .first()
    )
    if active_payment is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A payment is already in progress for this order.",
        )

    # ── Load merchant config ───────────────────────────────────────────────
    config = _get_config_for_restaurant(db, restaurant_id, provider)

    # ── DB TRANSACTION: INSERT Payment + PaymentAttempt ───────────────────
    # No external HTTP inside this block.
    payment = Payment(
        order_id=order_id,
        restaurant_id=restaurant_id,
        status="pending",
        amount=order.total_amount,       # server-authoritative
        currency=order.currency,         # server-authoritative
        provider=provider,
        idempotency_key=idempotency_key,
    )
    db.add(payment)
    try:
        db.flush()  # get payment.id before creating attempt
    except IntegrityError as exc:
        db.rollback()
        err = str(exc).lower()
        if "uq_payments_order_idempotency" in err:
            # Race on idempotency key — return existing
            db.rollback()
            existing = (
                db.query(Payment)
                .filter(
                    Payment.order_id == order_id,
                    Payment.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing:
                checkout_url = _generate_checkout_url(existing, config)
                return existing, checkout_url
        if "uq_payments_order_active" in err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment is already in progress for this order.",
            )
        raise

    attempt = PaymentAttempt(
        payment_id=payment.id,
        provider=provider,
        amount=order.total_amount,
        currency=order.currency,
        status="pending",
    )
    db.add(attempt)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        err = str(exc).lower()
        if "uq_payments_order_active" in err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment is already in progress for this order.",
            )
        if "uq_payments_order_idempotency" in err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotency key for a different order.",
            )
        raise

    db.refresh(payment)

    # ── Generate checkout URL locally (no external HTTP) ──────────────────
    checkout_url = _generate_checkout_url(payment, config)
    return payment, checkout_url


def _generate_checkout_url(payment: Payment, config: RestaurantPaymentConfig) -> str:
    """Generate provider checkout URL. Pure local operation — no HTTP calls."""
    provider_adapter = get_provider(payment.provider)
    return provider_adapter.generate_checkout_url(
        payment_id=payment.id,
        amount_tiyins=payment.amount,
        currency=payment.currency,
        merchant_id=config.merchant_id,
        service_id=config.service_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# PAYME CALLBACK PROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def process_payme_callback(
    db: Session,
    request_headers: dict[str, str],
    request_body: dict[str, Any],
) -> dict:
    """
    Handle one Payme JSON-RPC 2.0 callback.

    Flow:
      1. Resolve config by Basic Auth merchant_id → restaurant_id
      2. Authenticate Basic Auth against decrypted secret
      3. Parse JSON-RPC method
      4. Business logic per method (some read-only, some state transitions)
      5. Return JSON-RPC result (always HTTP 200)

    Authentication and parsing happen OUTSIDE the DB transaction.
    State transitions happen INSIDE a transaction with SELECT FOR UPDATE.
    """
    from .providers.payme import PaymeProvider, _E_AUTH, _E_ACCOUNT, _E_AMOUNT
    from .providers.payme import _E_TX_NOT_FOUND, _E_CANNOT_CANCEL, _E_SYSTEM
    from .providers.payme import _E_CANNOT_PERFORM
    from .providers.payme import (
        _STATE_CREATED, _STATE_PERFORMED,
        _STATE_CANCELLED_BEFORE_PERFORM, _STATE_CANCELLED_AFTER_PERFORM,
    )

    adapter: PaymeProvider = get_provider("payme")  # type: ignore[assignment]

    # ── Step 1: Resolve config by merchant_id from Basic Auth ──────────────
    auth_header = request_headers.get("authorization", "")
    try:
        import base64 as _b64
        encoded = auth_header[6:]
        decoded = _b64.b64decode(encoded).decode("utf-8")
        merchant_id_from_auth, _ = decoded.split(":", 1)
    except Exception:
        return adapter.build_error_response(None, _E_AUTH, "Invalid Basic Auth header")

    try:
        config = _resolve_config_by_merchant_id(db, merchant_id_from_auth, "payme")
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    # ── Step 2: Authenticate ───────────────────────────────────────────────
    try:
        decrypted_secret = decrypt_token(config.encrypted_secret)
    except Exception:
        logger.error(
            "Payme: Fernet decrypt failed for config_id=%s restaurant_id=%s",
            config.id, config.restaurant_id,
        )
        return adapter.build_error_response(None, _E_SYSTEM, "Internal configuration error")

    try:
        adapter.authenticate(request_headers, request_body, decrypted_secret, config.merchant_id)
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    # ── Step 3: Parse ──────────────────────────────────────────────────────
    try:
        callback = adapter.parse_callback(request_body)
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    # ── Step 4: Dispatch by method ─────────────────────────────────────────
    restaurant_id = config.restaurant_id

    if callback.method == CALLBACK_METHOD_CHECK_PERFORM:
        return _payme_check_perform(db, adapter, callback, restaurant_id)

    elif callback.method == CALLBACK_METHOD_CREATE:
        return _payme_create_transaction(db, adapter, callback, restaurant_id)

    elif callback.method == CALLBACK_METHOD_PERFORM:
        return _payme_perform_transaction(db, adapter, callback, restaurant_id)

    elif callback.method == CALLBACK_METHOD_CANCEL:
        return _payme_cancel_transaction(db, adapter, callback, restaurant_id)

    elif callback.method == CALLBACK_METHOD_CHECK_STATUS:
        return _payme_check_transaction(db, adapter, callback, restaurant_id)

    return adapter.build_error_response(callback, -32601, "Method not found")


def _payme_check_perform(db, adapter, callback, restaurant_id):
    from .providers.payme import _E_ACCOUNT, _E_AMOUNT
    try:
        payment_id = int(callback.payment_ref)
    except (ValueError, TypeError):
        return adapter.build_account_error_response(callback, "order_id", "Invalid order_id")

    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            Payment.restaurant_id == restaurant_id,
        )
        .first()
    )
    if payment is None:
        return adapter.build_account_error_response(callback, "order_id", "Order not found")

    if payment.status in ("paid", "failed", "cancelled"):
        return adapter.build_account_error_response(
            callback, "order_id", f"Payment is {payment.status}"
        )
    if callback.amount_tiyins != payment.amount:
        return adapter.build_error_response(callback, _E_AMOUNT, "Amount mismatch")

    return adapter.build_success_response(callback, payment.id)


def _payme_create_transaction(db, adapter, callback, restaurant_id):
    from .providers.payme import _E_ACCOUNT, _E_AMOUNT, _E_CANNOT_PERFORM
    from .providers.payme import _STATE_CREATED
    from sqlalchemy.sql import func as sqlfunc

    try:
        payment_id = int(callback.payment_ref)
    except (ValueError, TypeError):
        return adapter.build_account_error_response(callback, "order_id", "Invalid order_id")

    # SELECT FOR UPDATE — wait, not NOWAIT (idempotency requires seeing final state)
    try:
        payment = (
            db.query(Payment)
            .filter(
                Payment.id == payment_id,
                Payment.restaurant_id == restaurant_id,
            )
            .with_for_update()
            .first()
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Payme CreateTransaction: DB error payment_id=%s", payment_id)
        return adapter.build_error_response(callback, -32400, "System error")

    if payment is None:
        db.rollback()
        return adapter.build_account_error_response(callback, "order_id", "Order not found")

    # Idempotency: same Payme tx_id already recorded → return same result
    if (
        payment.provider_transaction_id == callback.provider_transaction_id
        and payment.status in ("processing", "paid")
    ):
        db.rollback()
        created_ms = int(payment.created_at.timestamp() * 1000) if payment.created_at else 0
        return adapter.build_success_response(
            callback, payment.id, created_at_ts=created_ms
        )

    if payment.status != "pending":
        db.rollback()
        return adapter.build_error_response(
            callback, _E_CANNOT_PERFORM,
            f"Payment cannot be created in state '{payment.status}'",
        )

    if callback.amount_tiyins != payment.amount:
        db.rollback()
        return adapter.build_error_response(callback, _E_AMOUNT, "Amount mismatch")

    payment.status = "processing"
    payment.provider_transaction_id = callback.provider_transaction_id
    payment.updated_at = _now_utc()

    attempt = PaymentAttempt(
        payment_id=payment.id,
        provider="payme",
        provider_transaction_id=callback.provider_transaction_id,
        amount=payment.amount,
        currency=payment.currency,
        status="pending",
    )
    db.add(attempt)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Payme CreateTransaction: commit failed payment_id=%s", payment_id)
        return adapter.build_error_response(callback, -32400, "System error")

    db.refresh(payment)
    created_ms = int(payment.created_at.timestamp() * 1000) if payment.created_at else 0
    return adapter.build_success_response(callback, payment.id, created_at_ts=created_ms)


def _payme_perform_transaction(db, adapter, callback, restaurant_id):
    from .providers.payme import _E_TX_NOT_FOUND, _E_CANNOT_PERFORM, _STATE_PERFORMED

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider_transaction_id == callback.provider_transaction_id,
            Payment.restaurant_id == restaurant_id,
            Payment.provider == "payme",
        )
        .with_for_update()
        .first()
    )
    if payment is None:
        db.rollback()
        return adapter.build_error_response(callback, _E_TX_NOT_FOUND, "Transaction not found")

    # Idempotency: already PAID → return same successful result
    if payment.status == "paid":
        db.rollback()
        paid_ms = int(payment.paid_at.timestamp() * 1000) if payment.paid_at else 0
        cb_copy = ParsedCallback(
            provider=callback.provider,
            method=callback.method,
            payment_ref=str(payment.id),
            amount_tiyins=payment.amount,
            provider_transaction_id=callback.provider_transaction_id,
        )
        return adapter.build_success_response(cb_copy, payment.id, paid_at_ts=paid_ms)

    if payment.status != "processing":
        db.rollback()
        return adapter.build_error_response(
            callback, _E_CANNOT_PERFORM,
            f"Cannot perform: payment status is '{payment.status}'",
        )

    now = _now_utc()
    payment.status = "paid"
    payment.paid_at = now
    payment.updated_at = now

    # Update Order.paid_at projection
    order = db.query(Order).filter(Order.id == payment.order_id).first()
    if order:
        order.paid_at = now

    # Update PaymentAttempt
    attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.payment_id == payment.id,
            PaymentAttempt.provider_transaction_id == callback.provider_transaction_id,
        )
        .first()
    )
    if attempt:
        attempt.status = "success"
        attempt.updated_at = now

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Payme PerformTransaction: commit failed payment_id=%s", payment.id)
        return adapter.build_error_response(callback, -32400, "System error")

    db.refresh(payment)
    paid_ms = int(payment.paid_at.timestamp() * 1000)
    cb_with_ref = ParsedCallback(
        provider=callback.provider,
        method=callback.method,
        payment_ref=str(payment.id),
        amount_tiyins=payment.amount,
        provider_transaction_id=callback.provider_transaction_id,
    )
    return adapter.build_success_response(cb_with_ref, payment.id, paid_at_ts=paid_ms)


def _payme_cancel_transaction(db, adapter, callback, restaurant_id):
    from .providers.payme import _E_TX_NOT_FOUND, _E_CANNOT_CANCEL
    from .providers.payme import (
        _STATE_CANCELLED_BEFORE_PERFORM, _STATE_CANCELLED_AFTER_PERFORM,
    )

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider_transaction_id == callback.provider_transaction_id,
            Payment.restaurant_id == restaurant_id,
            Payment.provider == "payme",
        )
        .with_for_update()
        .first()
    )
    if payment is None:
        db.rollback()
        return adapter.build_error_response(callback, _E_TX_NOT_FOUND, "Transaction not found")

    # Idempotency: already cancelled (FAILED) → return same cancel result
    if payment.status == "failed":
        db.rollback()
        cancel_state = (
            _STATE_CANCELLED_AFTER_PERFORM
            if payment.failure_reason == "cancelled_after_perform"
            else _STATE_CANCELLED_BEFORE_PERFORM
        )
        cb_copy = ParsedCallback(
            provider=callback.provider,
            method=callback.method,
            payment_ref=str(payment.id),
            amount_tiyins=payment.amount,
            provider_transaction_id=callback.provider_transaction_id,
            payme_cancel_state=cancel_state,
        )
        return adapter.build_success_response(cb_copy, payment.id)

    # Determine state: -2 if cancellation after perform (post-PAID)
    if payment.status == "paid":
        cancel_state = _STATE_CANCELLED_AFTER_PERFORM
        failure_reason = "cancelled_after_perform"
    elif payment.status in ("pending", "processing"):
        cancel_state = _STATE_CANCELLED_BEFORE_PERFORM
        failure_reason = "cancelled_by_payme"
    else:
        db.rollback()
        return adapter.build_error_response(
            callback, _E_CANNOT_CANCEL, f"Cannot cancel from state '{payment.status}'"
        )

    now = _now_utc()
    payment.status = "failed"
    payment.failure_reason = failure_reason
    payment.updated_at = now

    attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.payment_id == payment.id,
            PaymentAttempt.provider_transaction_id == callback.provider_transaction_id,
        )
        .first()
    )
    if attempt:
        attempt.status = "cancelled"
        attempt.failure_reason = failure_reason
        attempt.updated_at = now

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Payme CancelTransaction: commit failed payment_id=%s", payment.id)
        return adapter.build_error_response(callback, -32400, "System error")

    cb_copy = ParsedCallback(
        provider=callback.provider,
        method=callback.method,
        payment_ref=str(payment.id),
        amount_tiyins=payment.amount,
        provider_transaction_id=callback.provider_transaction_id,
        payme_cancel_state=cancel_state,
    )
    return adapter.build_success_response(cb_copy, payment.id)


def _payme_check_transaction(db, adapter, callback, restaurant_id):
    from .providers.payme import _E_TX_NOT_FOUND

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider_transaction_id == callback.provider_transaction_id,
            Payment.restaurant_id == restaurant_id,
            Payment.provider == "payme",
        )
        .first()
    )
    if payment is None:
        return adapter.build_error_response(callback, _E_TX_NOT_FOUND, "Transaction not found")

    paid_ms = int(payment.paid_at.timestamp() * 1000) if payment.paid_at else 0
    created_ms = int(payment.created_at.timestamp() * 1000) if payment.created_at else 0
    cb_copy = ParsedCallback(
        provider=callback.provider,
        method=callback.method,
        payment_ref=str(payment.id),
        amount_tiyins=payment.amount,
        provider_transaction_id=callback.provider_transaction_id,
    )
    return adapter.build_success_response(
        cb_copy, payment.id, paid_at_ts=paid_ms, created_at_ts=created_ms
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLICK CALLBACK PROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def process_click_callback(
    db: Session,
    request_body: dict[str, Any],
) -> dict:
    """
    Handle one Click SHOP API callback (Prepare or Complete).

    Flow:
      1. Resolve config by service_id from body → restaurant_id
      2. Authenticate MD5 sign_string
      3. Parse action (Prepare / Complete)
      4. State transition inside DB transaction (SELECT FOR UPDATE)
      5. Return HTTP 200 JSON always
    """
    from .providers.click import ClickProvider, _E_SIGN, _E_NOT_FOUND, _E_PAID
    from .providers.click import _E_CANCELLED, _E_TX_MISSING, _E_AMOUNT

    adapter: ClickProvider = get_provider("click")  # type: ignore[assignment]

    # ── Step 1: Resolve config by service_id ──────────────────────────────
    service_id = str(request_body.get("service_id", ""))
    try:
        config = _resolve_config_by_service_id(db, service_id, "click")
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    restaurant_id = config.restaurant_id

    # ── Step 2: Authenticate ───────────────────────────────────────────────
    try:
        decrypted_secret = decrypt_token(config.encrypted_secret)
    except Exception:
        logger.error(
            "Click: Fernet decrypt failed config_id=%s restaurant_id=%s",
            config.id, restaurant_id,
        )
        return adapter.build_error_response(None, -8, "Internal configuration error")

    try:
        adapter.authenticate({}, request_body, decrypted_secret, config.merchant_id)
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    # ── Step 3: Parse ──────────────────────────────────────────────────────
    try:
        callback = adapter.parse_callback(request_body)
    except ProviderCallbackError as exc:
        return adapter.build_error_response(None, exc.error_code, exc.message)

    # ── Step 4: Dispatch ───────────────────────────────────────────────────
    if callback.method == CALLBACK_METHOD_PREPARE:
        return _click_prepare(db, adapter, callback, restaurant_id)
    elif callback.method == CALLBACK_METHOD_COMPLETE:
        return _click_complete(db, adapter, callback, restaurant_id)

    return adapter.build_error_response(callback, -3, "Unknown action")


def _click_prepare(db, adapter, callback, restaurant_id):
    from .providers.click import _E_NOT_FOUND, _E_PAID, _E_CANCELLED, _E_AMOUNT

    try:
        payment_id = int(callback.payment_ref)
    except (ValueError, TypeError):
        return adapter.build_error_response(callback, _E_NOT_FOUND, "Invalid merchant_trans_id")

    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            Payment.restaurant_id == restaurant_id,
        )
        .with_for_update()
        .first()
    )
    if payment is None:
        db.rollback()
        return adapter.build_error_response(callback, _E_NOT_FOUND, "Payment not found")

    # Idempotency: already in processing with same click_trans_id
    if (
        payment.status == "processing"
        and payment.provider_transaction_id == callback.provider_transaction_id
    ):
        db.rollback()
        return adapter.build_success_response(callback, payment.id)

    if payment.status == "paid":
        db.rollback()
        return adapter.build_error_response(callback, _E_PAID, "Already paid")

    if payment.status in ("failed", "cancelled"):
        db.rollback()
        return adapter.build_error_response(callback, _E_CANCELLED, "Transaction cancelled")

    if payment.status != "pending":
        db.rollback()
        return adapter.build_error_response(callback, -8, f"Unexpected status: {payment.status}")

    if callback.amount_tiyins != payment.amount:
        db.rollback()
        return adapter.build_error_response(callback, _E_AMOUNT, "Amount mismatch")

    now = _now_utc()
    payment.status = "processing"
    payment.provider_transaction_id = callback.provider_transaction_id
    payment.updated_at = now

    attempt = PaymentAttempt(
        payment_id=payment.id,
        provider="click",
        provider_transaction_id=callback.provider_transaction_id,
        amount=payment.amount,
        currency=payment.currency,
        status="pending",
    )
    db.add(attempt)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Click Prepare: commit failed payment_id=%s", payment_id)
        return adapter.build_error_response(callback, -8, "System error")

    db.refresh(payment)
    return adapter.build_success_response(callback, payment.id)


def _click_complete(db, adapter, callback, restaurant_id):
    from .providers.click import _E_NOT_FOUND, _E_PAID, _E_CANCELLED, _E_TX_MISSING

    try:
        payment_id = int(callback.payment_ref)
    except (ValueError, TypeError):
        return adapter.build_error_response(callback, _E_TX_MISSING, "Invalid merchant_prepare_id")

    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            Payment.restaurant_id == restaurant_id,
        )
        .with_for_update()
        .first()
    )
    if payment is None:
        db.rollback()
        return adapter.build_error_response(callback, _E_TX_MISSING, "Payment not found")

    # Idempotency: already PAID
    if payment.status == "paid":
        db.rollback()
        return adapter.build_error_response(callback, _E_PAID, "Already paid")

    # Idempotency: already FAILED/CANCELLED
    if payment.status in ("failed", "cancelled"):
        db.rollback()
        return adapter.build_error_response(callback, _E_CANCELLED, "Transaction cancelled")

    if payment.status != "processing":
        db.rollback()
        return adapter.build_error_response(
            callback, -8, f"Unexpected payment status: {payment.status}"
        )

    now = _now_utc()
    click_error = callback.click_error if callback.click_error is not None else 0

    if click_error == 0:
        # Success: PROCESSING → PAID
        payment.status = "paid"
        payment.paid_at = now
        payment.updated_at = now

        # Order.paid_at projection
        order = db.query(Order).filter(Order.id == payment.order_id).first()
        if order:
            order.paid_at = now

        attempt_status = "success"
        attempt_failure = None
    else:
        # Cancellation from Click: PROCESSING → FAILED
        payment.status = "failed"
        payment.failure_reason = f"click_error={click_error}"
        payment.updated_at = now

        attempt_status = "cancelled"
        attempt_failure = f"click_error={click_error}"

    attempt = (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.payment_id == payment.id,
            PaymentAttempt.provider_transaction_id == callback.provider_transaction_id,
        )
        .first()
    )
    if attempt:
        attempt.status = attempt_status
        attempt.failure_reason = attempt_failure
        attempt.updated_at = now

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Click Complete: commit failed payment_id=%s", payment_id)
        return adapter.build_error_response(callback, -8, "System error")

    if click_error == 0:
        db.refresh(payment)
        return adapter.build_success_response(callback, payment.id)
    else:
        return adapter.build_error_response(callback, _E_CANCELLED, f"Cancelled by Click: error={click_error}")


# ──────────────────────────────────────────────────────────────────────────────
# TYPE HINT FIX (Any imported at module level)
# ──────────────────────────────────────────────────────────────────────────────
from typing import Any  # noqa: E402 — re-export for function signatures above
