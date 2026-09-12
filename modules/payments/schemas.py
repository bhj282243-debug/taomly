"""
modules/payments/schemas.py — Taomly Platform
Phase 8: Payment domain Pydantic schemas.

Security rules:
  - Client never supplies amount, currency, restaurant_id — sourced from Order.
  - PaymentResponse never exposes encrypted_secret or merchant_key.
  - status='paid' cannot be set via client API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# REQUEST SCHEMAS
# ──────────────────────────────────────────────────────────────────────────────

class PaymentCreateRequest(BaseModel):
    """
    Request body for POST /api/payments.

    The client supplies only the intent — never the financial values.
    amount and currency are resolved server-side from Order.
    """
    order_id: int = Field(..., description="Order to pay for")
    provider: str = Field(
        ...,
        description="Payment provider: 'payme', 'click', or 'sandbox' (test only)",
    )
    idempotency_key: Optional[str] = Field(
        None,
        max_length=64,
        description="Opaque client-generated key for safe retries",
    )


# ──────────────────────────────────────────────────────────────────────────────
# RESPONSE SCHEMAS
# ──────────────────────────────────────────────────────────────────────────────

class PaymentResponse(BaseModel):
    """
    Response for POST /api/payments and GET /api/payments/{payment_id}.

    Never exposes: encrypted_secret, merchant_key, provider secrets.
    """
    payment_id: int
    order_id: int
    restaurant_id: int
    status: str
    amount: int            # Integer tiyins
    currency: str
    provider: str
    checkout_url: str
    idempotency_key: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaymentStatusResponse(BaseModel):
    """Lightweight status check response."""
    payment_id: int
    order_id: int
    status: str
    amount: int
    currency: str
    provider: str
    paid_at: Optional[datetime] = None

    class Config:
        from_attributes = True
