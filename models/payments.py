"""
models/payments.py — Taomly Platform
Phase 8: Payment Engine — Customer Order Payments.

Entities:
  RestaurantPaymentConfig — per-restaurant, per-provider merchant credentials.
  Payment                 — source of truth for a payment against an Order.
  PaymentAttempt          — audit trail of individual payment interactions.

Business model:
  Customer → Taomly Order → Payment → Click/Payme → Restaurant merchant account
  Taomly never holds customer funds. No custody, no wallet, no settlement.

Money convention: all amounts in INTEGER TIYINS (same as Order.total_amount).
Click amounts (soums float) are normalised in the provider adapter, not here.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


# ──────────────────────────────────────────────────────────────────────────────
# RESTAURANT PAYMENT CONFIG
# ──────────────────────────────────────────────────────────────────────────────

class RestaurantPaymentConfig(Base):
    """
    Per-restaurant, per-provider merchant payment configuration.

    One restaurant may have at most one active config per provider:
      UNIQUE(restaurant_id, provider)

    Fernet-encrypted secrets:
      Payme  → encrypted_secret = Fernet(merchant_key)
      Click  → encrypted_secret = Fernet(secret_key)
    Use auth.encrypt_token / auth.decrypt_token — same infrastructure as bot tokens.

    Field usage by provider:
      Payme  callback routing  → merchant_id  (Basic Auth username)
      Payme  auth verify       → decrypt(encrypted_secret) == Basic Auth password
      Payme  checkout URL      → m=merchant_id
      Click  callback routing  → service_id   (from POST body)
      Click  sign verify       → decrypt(encrypted_secret) as SECRET_KEY in MD5
      Click  checkout URL      → service_id + merchant_id

    NEVER return encrypted_secret in any API response.
    NEVER log decrypted secrets.
    """

    __tablename__ = "restaurant_payment_configs"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('payme', 'click')",
            name="ck_rpc_provider",
        ),
        # One config per provider per restaurant
        Index(
            "uq_rpc_restaurant_provider",
            "restaurant_id",
            "provider",
            unique=True,
        ),
        Index("ix_rpc_restaurant_id", "restaurant_id"),
        # Fast callback lookup: Payme by merchant_id, Click by service_id
        Index("ix_rpc_merchant_provider", "merchant_id", "provider"),
        Index("ix_rpc_service_provider", "service_id", "provider"),
    )

    id            = Column(BigInteger, primary_key=True)
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider      = Column(String(50), nullable=False)
    # Payme: merchant_id = callback routing + Basic Auth username + checkout URL
    # Click: merchant_id = checkout URL only (NOT used for callback routing)
    merchant_id   = Column(String(255), nullable=False)
    # Click only: service_id = callback routing + sign_string component
    # NULL for Payme
    service_id    = Column(String(100), nullable=True)
    # Fernet-encrypted. Never expose raw value.
    encrypted_secret = Column(Text, nullable=False)
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at    = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    restaurant = relationship("Restaurant", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<RestaurantPaymentConfig id={self.id} "
            f"restaurant_id={self.restaurant_id} provider={self.provider!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# PAYMENT
# ──────────────────────────────────────────────────────────────────────────────

class Payment(Base):
    """
    Source of truth for a customer payment against an Order.

    State machine:
      PENDING → PROCESSING → PAID (terminal)
      PENDING → FAILED
      PENDING → CANCELLED
      PROCESSING → FAILED

    DB-level invariants:
      1. At most one active (PENDING or PROCESSING) Payment per Order:
           PARTIAL UNIQUE INDEX WHERE status IN ('pending','processing')
      2. Idempotency: same (order_id, idempotency_key) → same Payment:
           PARTIAL UNIQUE INDEX WHERE idempotency_key IS NOT NULL
      3. amount >= 0 — sourced from Order.total_amount (never from client).
      4. currency — sourced from Order.currency (never from client).

    Security invariant:
      PAID status can only be set by server-side provider callback verification.
      Client API cannot set status=paid.
    """

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','paid','failed','cancelled')",
            name="ck_payments_status",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_payments_amount_nonneg",
        ),
        CheckConstraint(
            "currency IN ('UZS','KZT','RUB','USD','TRY','AED')",
            name="ck_payments_currency",
        ),
        CheckConstraint(
            "provider IN ('payme','click','sandbox')",
            name="ck_payments_provider",
        ),
        Index("ix_payments_order_id", "order_id"),
        Index("ix_payments_restaurant_id", "restaurant_id"),
    )

    id            = Column(BigInteger, primary_key=True)
    order_id      = Column(
        BigInteger,
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    restaurant_id = Column(
        BigInteger,
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 'pending' | 'processing' | 'paid' | 'failed' | 'cancelled'
    status        = Column(String(20), nullable=False, default="pending")
    # Integer tiyins — copied from Order.total_amount at creation time.
    amount        = Column(Integer, nullable=False)
    # Copied from Order.currency at creation time.
    currency      = Column(String(10), nullable=False)
    # 'payme' | 'click' | 'sandbox'
    provider      = Column(String(50), nullable=False)
    # Set when provider assigns its own transaction ID:
    #   Payme: params.id (24-char ObjectId string)
    #   Click: click_trans_id (bigint as string)
    provider_transaction_id = Column(String(255), nullable=True)
    # Client-supplied opaque key for Payment creation idempotency.
    # Partial UNIQUE INDEX: (order_id, idempotency_key) WHERE NOT NULL.
    idempotency_key = Column(String(64), nullable=True)
    created_at    = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at    = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Set when Payment transitions to PAID. Also written to Order.paid_at.
    paid_at       = Column(TIMESTAMP(timezone=True), nullable=True)
    failure_reason = Column(Text, nullable=True)

    order       = relationship("Order", lazy="select")
    restaurant  = relationship("Restaurant", lazy="select")
    attempts    = relationship(
        "PaymentAttempt",
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id} order_id={self.order_id} "
            f"status={self.status!r} amount={self.amount} "
            f"provider={self.provider!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# PAYMENT ATTEMPT
# ──────────────────────────────────────────────────────────────────────────────

class PaymentAttempt(Base):
    """
    Audit trail for provider interactions within a Payment.

    One Payment may have multiple attempts (e.g. Payme callback sequence:
    CreateTransaction, PerformTransaction, etc.).

    PaymentAttempt is NOT the source of truth — Payment.status is.

    NEVER store:
      card number, CVV, PIN, card token, decrypted provider secrets.
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','success','failed','cancelled')",
            name="ck_pa_status",
        ),
        Index("ix_payment_attempts_payment_id", "payment_id"),
    )

    id            = Column(BigInteger, primary_key=True)
    payment_id    = Column(
        BigInteger,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider      = Column(String(50), nullable=False)
    # Provider's transaction identifier for this attempt.
    provider_transaction_id = Column(String(255), nullable=True)
    # Amount in tiyins at the time of this attempt.
    amount        = Column(Integer, nullable=False)
    currency      = Column(String(10), nullable=False)
    # 'pending' | 'success' | 'failed' | 'cancelled'
    status        = Column(String(20), nullable=False, default="pending")
    created_at    = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at    = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    failure_reason = Column(Text, nullable=True)

    payment = relationship("Payment", back_populates="attempts", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<PaymentAttempt id={self.id} payment_id={self.payment_id} "
            f"status={self.status!r} provider={self.provider!r}>"
        )
