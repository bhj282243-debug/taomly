"""
modules/payments/tests/test_payments.py — Taomly Platform
Phase 8: Payment Engine test suite.

Coverage:
  A. Payment creation (valid, invalid, idempotency, tenant isolation)
  B. State machine (valid/invalid transitions, PAID terminal)
  C. Payme callbacks (CheckPerformTransaction, CreateTransaction,
     PerformTransaction, CancelTransaction, CheckTransaction, idempotency, auth)
  D. Click callbacks (Prepare, Complete success/failure, idempotency, sign)
  E. Sandbox provider flow
  F. Concurrency (marked @pytest.mark.postgres — PostgreSQL only)
  G. Security (tenant isolation, secret not in response, PAID not client-settable)
  H. Checkout URL format

Fixtures extend existing conftest.py (restaurant, restaurant2, location,
location2, tg_user, tg_user2, db, checkout_client patterns).
"""

from __future__ import annotations

import base64
import hashlib
import threading
from typing import Generator, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api import app
from auth import encrypt_token, get_telegram_user
from database import get_db
from models import Agency, Order, Restaurant, Location
from models.payments import Payment, PaymentAttempt, RestaurantPaymentConfig

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_PAYME_MERCHANT_ID  = "test_payme_merchant"
_PAYME_MERCHANT_KEY = "test_payme_key_secret"
_CLICK_SERVICE_ID   = "77777"
_CLICK_MERCHANT_ID  = "88888"
_CLICK_SECRET_KEY   = "click_secret_for_tests"

_ORDER_AMOUNT_TIYINS = 150_000  # 1500 UZS in tiyins
_CLICK_AMOUNT_SOUMS  = 1500.0   # same in soums (float, as Click sends)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _basic_auth(merchant_id: str, merchant_key: str) -> str:
    raw = f"{merchant_id}:{merchant_key}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _click_sign_prepare(
    click_trans_id, service_id, secret_key,
    merchant_trans_id, amount, action, sign_time,
) -> str:
    raw = (
        str(click_trans_id) + str(service_id) + secret_key
        + str(merchant_trans_id) + str(amount) + str(action) + str(sign_time)
    )
    return hashlib.md5(raw.encode()).hexdigest()


def _click_sign_complete(
    click_trans_id, service_id, secret_key,
    merchant_trans_id, merchant_prepare_id, amount, action, sign_time,
) -> str:
    raw = (
        str(click_trans_id) + str(service_id) + secret_key
        + str(merchant_trans_id) + str(merchant_prepare_id)
        + str(amount) + str(action) + str(sign_time)
    )
    return hashlib.md5(raw.encode()).hexdigest()


def _payme_rpc(method: str, params: dict, rpc_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def payme_config(db: Session, restaurant: Restaurant) -> RestaurantPaymentConfig:
    """Payme RestaurantPaymentConfig for main test restaurant."""
    cfg = RestaurantPaymentConfig(
        restaurant_id=restaurant.id,
        provider="payme",
        merchant_id=_PAYME_MERCHANT_ID,
        service_id=None,
        encrypted_secret=encrypt_token(_PAYME_MERCHANT_KEY),
        is_active=True,
    )
    db.add(cfg)
    db.flush()
    return cfg


@pytest.fixture
def click_config(db: Session, restaurant: Restaurant) -> RestaurantPaymentConfig:
    """Click RestaurantPaymentConfig for main test restaurant."""
    cfg = RestaurantPaymentConfig(
        restaurant_id=restaurant.id,
        provider="click",
        merchant_id=_CLICK_MERCHANT_ID,
        service_id=_CLICK_SERVICE_ID,
        encrypted_secret=encrypt_token(_CLICK_SECRET_KEY),
        is_active=True,
    )
    db.add(cfg)
    db.flush()
    return cfg


@pytest.fixture
def payme_config2(db: Session, restaurant2: Restaurant) -> RestaurantPaymentConfig:
    """Payme config for restaurant2 (tenant isolation tests)."""
    cfg = RestaurantPaymentConfig(
        restaurant_id=restaurant2.id,
        provider="payme",
        merchant_id="other_merchant_id",
        service_id=None,
        encrypted_secret=encrypt_token("other_merchant_key"),
        is_active=True,
    )
    db.add(cfg)
    db.flush()
    return cfg


@pytest.fixture
def accepted_order(db: Session, restaurant: Restaurant, location: Location) -> Order:
    """An Order in 'accepted' status — payable."""
    order = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_name="Test Customer",
        client_phone="+998901234567",
        order_type="takeaway",
        total_amount=_ORDER_AMOUNT_TIYINS,
        currency="UZS",
        status="accepted",
    )
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def accepted_order2(db: Session, restaurant2: Restaurant, location2: Location) -> Order:
    """Order for restaurant2 (tenant isolation)."""
    order = Order(
        restaurant_id=restaurant2.id,
        location_id=location2.id,
        client_name="Other Customer",
        client_phone="+998900000000",
        order_type="takeaway",
        total_amount=200_000,
        currency="UZS",
        status="accepted",
    )
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def pending_payment(
    db: Session,
    accepted_order: Order,
    payme_config: RestaurantPaymentConfig,
) -> Payment:
    """A Payment in PENDING status."""
    p = Payment(
        order_id=accepted_order.id,
        restaurant_id=accepted_order.restaurant_id,
        status="pending",
        amount=accepted_order.total_amount,
        currency=accepted_order.currency,
        provider="payme",
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def processing_payment(
    db: Session,
    pending_payment: Payment,
) -> Payment:
    """A Payment in PROCESSING status with a provider_transaction_id."""
    pending_payment.status = "processing"
    pending_payment.provider_transaction_id = "payme_tx_001"
    db.flush()
    return pending_payment


@pytest.fixture
def payment_client(db: Session, restaurant: Restaurant, tg_user) -> Generator:
    """TestClient for payment creation (Telegram Mini App customer)."""
    def override_db():
        yield db

    def override_tg():
        return tg_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_telegram_user] = override_tg

    headers = {"X-Restaurant-Id": str(restaurant.id)}
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def payme_client(db: Session) -> Generator:
    """TestClient for Payme callback (no auth override — auth via Basic Auth)."""
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def click_client(db: Session) -> Generator:
    """TestClient for Click callback."""
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def sandbox_client(db: Session, restaurant: Restaurant, tg_user) -> Generator:
    """TestClient for sandbox callback."""
    def override_db():
        yield db

    def override_tg():
        return tg_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_telegram_user] = override_tg

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# A. PAYMENT CREATION
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentCreation:

    def test_valid_payment_payme(
        self, payment_client, accepted_order, payme_config
    ):
        """Valid Order + Payme config → Payment(PENDING) + checkout_url."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["amount"] == _ORDER_AMOUNT_TIYINS
        assert data["currency"] == "UZS"
        assert data["provider"] == "payme"
        # URL contains "paycom.uz" in both sandbox and production:
        #   sandbox:    https://checkout.test.paycom.uz/<b64>
        #   production: https://checkout.paycom.uz/<b64>
        # Check the invariant (paycom.uz), not the env-specific hostname.
        assert "paycom.uz" in data["checkout_url"]
        # Encoded payload must contain merchant_id, payment reference, and amount.
        decoded = base64.b64decode(
            data["checkout_url"].split("/")[-1]
        ).decode()
        assert f"ac.order_id={data['payment_id']}" in decoded, f"payment_id missing in: {decoded}"
        assert f"a={_ORDER_AMOUNT_TIYINS}" in decoded, f"amount missing in: {decoded}"
        assert f"m={_PAYME_MERCHANT_ID}" in decoded, f"merchant_id missing in: {decoded}"

    def test_valid_payment_click(
        self, payment_client, accepted_order, click_config
    ):
        """Valid Order + Click config → Payment(PENDING) + Click checkout_url."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "click",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert "my.click.uz" in data["checkout_url"]
        assert f"transaction_param={data['payment_id']}" in data["checkout_url"]

    def test_amount_from_order_not_client(
        self, payment_client, accepted_order, payme_config
    ):
        """Client cannot influence amount — sourced from Order.total_amount."""
        # Even if client sends garbage, it's ignored (not in schema)
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 201
        assert resp.json()["amount"] == _ORDER_AMOUNT_TIYINS

    def test_order_not_found(self, payment_client, payme_config):
        resp = payment_client.post("/api/payments/", json={
            "order_id": 999999,
            "provider": "payme",
        })
        assert resp.status_code == 404

    def test_order_belongs_to_other_restaurant(
        self, payment_client, accepted_order2, payme_config
    ):
        """Order from restaurant2 — customer of restaurant1 cannot pay."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order2.id,
            "provider": "payme",
        })
        assert resp.status_code == 404  # order not found for this restaurant

    def test_no_merchant_config(self, payment_client, accepted_order):
        """No RestaurantPaymentConfig for provider → 422."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 422

    def test_already_paid(
        self, payment_client, accepted_order, payme_config,
        db: Session,
    ):
        """Order already has PAID Payment → 409."""
        paid = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="paid",
            amount=accepted_order.total_amount,
            currency=accepted_order.currency,
            provider="payme",
        )
        db.add(paid)
        db.flush()
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 409

    def test_active_payment_conflict(
        self, payment_client, accepted_order, payme_config, pending_payment
    ):
        """Active PENDING Payment already exists → 409."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 409

    def test_idempotency_same_key_same_order(
        self, payment_client, accepted_order, payme_config
    ):
        """Same (order_id, idempotency_key) → same Payment returned."""
        body = {
            "order_id": accepted_order.id,
            "provider": "payme",
            "idempotency_key": "idem-key-001",
        }
        r1 = payment_client.post("/api/payments/", json=body)
        assert r1.status_code == 201
        r2 = payment_client.post("/api/payments/", json=body)
        assert r2.status_code == 201
        assert r1.json()["payment_id"] == r2.json()["payment_id"]

    def test_sandbox_blocked_in_production(
        self, payment_client, accepted_order, monkeypatch
    ):
        """Sandbox provider blocked when ENVIRONMENT=production."""
        import config
        monkeypatch.setattr(config.settings, "ENVIRONMENT", "production")
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "sandbox",
        })
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# B. PAYMENT STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentStateMachine:

    def test_pending_to_processing(self, db: Session, pending_payment: Payment):
        """PENDING → PROCESSING is valid."""
        pending_payment.status = "processing"
        db.flush()
        assert pending_payment.status == "processing"

    def test_processing_to_paid(self, db: Session, processing_payment: Payment):
        """PROCESSING → PAID is valid."""
        processing_payment.status = "paid"
        db.flush()
        assert processing_payment.status == "paid"

    def test_paid_is_terminal(self, db: Session, accepted_order, payme_config):
        """PAID → any transition not allowed by service."""
        from modules.payments.service import _VALID_TRANSITIONS
        assert _VALID_TRANSITIONS["paid"] == set()

    def test_failed_is_terminal(self):
        from modules.payments.service import _VALID_TRANSITIONS
        assert _VALID_TRANSITIONS["failed"] == set()

    def test_cancelled_is_terminal(self):
        from modules.payments.service import _VALID_TRANSITIONS
        assert _VALID_TRANSITIONS["cancelled"] == set()

    def test_client_cannot_set_paid(self, payment_client, payme_config):
        """There is no endpoint for client to set status=paid."""
        resp = payment_client.patch("/api/payments/1/status", json={"status": "paid"})
        # 404 or 405 — endpoint doesn't exist
        assert resp.status_code in (404, 405, 422)


# ─────────────────────────────────────────────────────────────────────────────
# C. PAYME CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymeCallbacks:

    def _auth_header(self, merchant_key: str = _PAYME_MERCHANT_KEY) -> dict:
        return {"Authorization": _basic_auth(_PAYME_MERCHANT_ID, merchant_key)}

    def test_invalid_basic_auth(self, payme_client, payme_config, pending_payment):
        """Wrong merchant_key → -32504."""
        body = _payme_rpc("CheckPerformTransaction", {
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme",
            json=body,
            headers={"Authorization": _basic_auth(_PAYME_MERCHANT_ID, "wrong_key")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32504

    def test_check_perform_valid(self, payme_client, payme_config, pending_payment):
        """CheckPerformTransaction: valid order + amount → allow: true."""
        body = _payme_rpc("CheckPerformTransaction", {
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["allow"] is True

    def test_check_perform_amount_mismatch(self, payme_client, payme_config, pending_payment):
        """CheckPerformTransaction: wrong amount → -31001."""
        body = _payme_rpc("CheckPerformTransaction", {
            "amount": _ORDER_AMOUNT_TIYINS + 1,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -31001

    def test_check_perform_not_found(self, payme_client, payme_config):
        """CheckPerformTransaction: unknown order_id → -31050."""
        body = _payme_rpc("CheckPerformTransaction", {
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": 999999},
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -31050

    def test_create_transaction(self, payme_client, payme_config, pending_payment):
        """CreateTransaction: valid → state:1, Payment → PROCESSING."""
        body = _payme_rpc("CreateTransaction", {
            "id": "payme_tx_aaa001",
            "time": 1700000000000,
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        result = resp.json().get("result", {})
        assert result.get("state") == 1
        assert result.get("transaction") == str(pending_payment.id)

    def test_create_transaction_idempotent(
        self, payme_client, payme_config, db: Session, pending_payment
    ):
        """Duplicate CreateTransaction with same Payme tx_id → idempotent."""
        body = _payme_rpc("CreateTransaction", {
            "id": "payme_tx_aaa002",
            "time": 1700000000000,
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": pending_payment.id},
        })
        r1 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        r2 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert r1.status_code == r2.status_code == 200
        assert r1.json().get("result", {}).get("state") == 1
        assert r2.json().get("result", {}).get("state") == 1

    def test_create_transaction_amount_mismatch(
        self, payme_client, payme_config, pending_payment
    ):
        """CreateTransaction: wrong amount → -31001."""
        body = _payme_rpc("CreateTransaction", {
            "id": "payme_tx_wrongamt",
            "time": 1700000000000,
            "amount": _ORDER_AMOUNT_TIYINS + 999,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -31001

    def test_perform_transaction(
        self, payme_client, payme_config, db: Session, processing_payment
    ):
        """PerformTransaction: valid PROCESSING → PAID, Order.paid_at set."""
        body = _payme_rpc("PerformTransaction", {"id": "payme_tx_001"})
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        result = resp.json().get("result", {})
        assert result.get("state") == 2

        db.expire_all()
        payment = db.query(Payment).filter(Payment.id == processing_payment.id).first()
        assert payment.status == "paid"
        assert payment.paid_at is not None

        order = db.query(Order).filter(Order.id == payment.order_id).first()
        assert order.paid_at is not None

    def test_perform_transaction_idempotent(
        self, payme_client, payme_config, db: Session, processing_payment
    ):
        """Duplicate PerformTransaction → idempotent (same state:2)."""
        body = _payme_rpc("PerformTransaction", {"id": "payme_tx_001"})
        r1 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        r2 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert r1.json().get("result", {}).get("state") == 2
        assert r2.json().get("result", {}).get("state") == 2

    def test_perform_transaction_not_found(self, payme_client, payme_config):
        """PerformTransaction: unknown Payme tx → -31003."""
        body = _payme_rpc("PerformTransaction", {"id": "unknown_tx_id"})
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -31003

    def test_cancel_transaction_from_processing(
        self, payme_client, payme_config, db: Session, processing_payment
    ):
        """CancelTransaction from PROCESSING → state:-1, Payment→FAILED."""
        body = _payme_rpc("CancelTransaction", {
            "id": "payme_tx_001",
            "reason": 1,
        })
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        result = resp.json().get("result", {})
        assert result.get("state") == -1

        db.expire_all()
        payment = db.query(Payment).filter(Payment.id == processing_payment.id).first()
        assert payment.status == "failed"

    def test_cancel_transaction_idempotent(
        self, payme_client, payme_config, db: Session, processing_payment
    ):
        """Duplicate CancelTransaction → same cancel response."""
        body = _payme_rpc("CancelTransaction", {"id": "payme_tx_001", "reason": 1})
        r1 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        r2 = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert r1.json().get("result", {}).get("state") in (-1, -2)
        assert r2.json().get("result", {}).get("state") in (-1, -2)

    def test_check_transaction(
        self, payme_client, payme_config, processing_payment
    ):
        """CheckTransaction: returns current state."""
        body = _payme_rpc("CheckTransaction", {"id": "payme_tx_001"})
        resp = payme_client.post(
            "/api/payments/payme", json=body, headers=self._auth_header()
        )
        assert resp.status_code == 200
        result = resp.json().get("result", {})
        assert "transaction" in result
        assert "state" in result

    def test_tenant_isolation_wrong_merchant(
        self, payme_client, payme_config2, pending_payment
    ):
        """Callback with merchant of restaurant2 cannot see payment of restaurant1."""
        body = _payme_rpc("CheckPerformTransaction", {
            "amount": _ORDER_AMOUNT_TIYINS,
            "account": {"order_id": pending_payment.id},
        })
        resp = payme_client.post(
            "/api/payments/payme",
            json=body,
            headers={"Authorization": _basic_auth("other_merchant_id", "other_merchant_key")},
        )
        assert resp.status_code == 200
        # Must return error — payment belongs to different restaurant
        data = resp.json()
        assert "error" in data or data.get("result", {}).get("allow") is not True


# ─────────────────────────────────────────────────────────────────────────────
# D. CLICK CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

class TestClickCallbacks:

    def _prepare_body(
        self,
        payment_id: int,
        click_trans_id: int = 1001,
        amount: float = _CLICK_AMOUNT_SOUMS,
        sign_override: Optional[str] = None,
    ) -> dict:
        sign_time = "20240101 120000"
        sign = sign_override or _click_sign_prepare(
            click_trans_id, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment_id, amount, 0, sign_time,
        )
        return {
            "click_trans_id": click_trans_id,
            "service_id": _CLICK_SERVICE_ID,
            "merchant_trans_id": str(payment_id),
            "amount": amount,
            "action": 0,
            "sign_time": sign_time,
            "sign_string": sign,
        }

    def _complete_body(
        self,
        payment_id: int,
        click_trans_id: int = 1001,
        amount: float = _CLICK_AMOUNT_SOUMS,
        error: int = 0,
        sign_override: Optional[str] = None,
    ) -> dict:
        sign_time = "20240101 120100"
        sign = sign_override or _click_sign_complete(
            click_trans_id, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment_id, payment_id, amount, 1, sign_time,
        )
        return {
            "click_trans_id": click_trans_id,
            "service_id": _CLICK_SERVICE_ID,
            "merchant_trans_id": str(payment_id),
            "merchant_prepare_id": payment_id,
            "amount": amount,
            "action": 1,
            "sign_time": sign_time,
            "sign_string": sign,
            "error": error,
        }

    def test_prepare_valid(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Prepare: valid → merchant_prepare_id returned, Payment→PROCESSING."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
        )
        db.add(payment)
        db.flush()

        resp = click_client.post(
            "/api/payments/click",
            json=self._prepare_body(payment.id),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") == 0
        assert data.get("merchant_prepare_id") == payment.id

        db.expire_all()
        p = db.query(Payment).filter(Payment.id == payment.id).first()
        assert p.status == "processing"

    def test_prepare_invalid_signature(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Prepare: wrong sign → error=-1."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
        )
        db.add(payment)
        db.flush()

        body = self._prepare_body(payment.id, sign_override="badbadbadbad")
        resp = click_client.post("/api/payments/click", json=body)
        assert resp.status_code == 200
        assert resp.json().get("error") == -1

    def test_prepare_amount_mismatch(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Prepare: amount in Click doesn't match Payment.amount → error=-2."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
        )
        db.add(payment)
        db.flush()

        # Wrong amount: 9999 soums instead of 1500
        body = self._prepare_body(payment.id, amount=9999.0)
        # Need to recalculate sign for wrong amount
        sign_time = "20240101 120000"
        sign = _click_sign_prepare(
            1001, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment.id, 9999.0, 0, sign_time,
        )
        body["sign_string"] = sign
        resp = click_client.post("/api/payments/click", json=body)
        assert resp.status_code == 200
        assert resp.json().get("error") == -2

    def test_prepare_idempotent(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Duplicate Prepare with same click_trans_id → idempotent success."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
        )
        db.add(payment)
        db.flush()

        body = self._prepare_body(payment.id)
        r1 = click_client.post("/api/payments/click", json=body)
        r2 = click_client.post("/api/payments/click", json=body)
        assert r1.json().get("error") == 0
        assert r2.json().get("error") == 0
        assert r1.json().get("merchant_prepare_id") == r2.json().get("merchant_prepare_id")

    def test_complete_success(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Complete (error=0): PROCESSING → PAID, Order.paid_at set."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="processing",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
            provider_transaction_id="1001",
        )
        db.add(payment)
        db.flush()

        resp = click_client.post(
            "/api/payments/click",
            json=self._complete_body(payment.id),
        )
        assert resp.status_code == 200
        assert resp.json().get("error") == 0

        db.expire_all()
        p = db.query(Payment).filter(Payment.id == payment.id).first()
        assert p.status == "paid"
        assert p.paid_at is not None

        order = db.query(Order).filter(Order.id == p.order_id).first()
        assert order.paid_at is not None

    def test_complete_failure(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Complete (error=-1): Click cancelled → Payment→FAILED."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="processing",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
            provider_transaction_id="1002",
        )
        db.add(payment)
        db.flush()

        body = self._complete_body(payment.id, click_trans_id=1002, error=-1)
        # Recalculate sign for error=-1 complete
        sign_time = "20240101 120100"
        sign = _click_sign_complete(
            1002, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment.id, payment.id, _CLICK_AMOUNT_SOUMS, 1, sign_time,
        )
        body["sign_string"] = sign
        resp = click_client.post("/api/payments/click", json=body)
        assert resp.status_code == 200

        db.expire_all()
        p = db.query(Payment).filter(Payment.id == payment.id).first()
        assert p.status == "failed"

    def test_complete_already_paid(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Complete on already PAID Payment → error=-4."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="paid",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
            provider_transaction_id="1003",
        )
        db.add(payment)
        db.flush()

        body = self._complete_body(payment.id, click_trans_id=1003)
        sign_time = "20240101 120100"
        sign = _click_sign_complete(
            1003, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment.id, payment.id, _CLICK_AMOUNT_SOUMS, 1, sign_time,
        )
        body["sign_string"] = sign
        resp = click_client.post("/api/payments/click", json=body)
        assert resp.status_code == 200
        assert resp.json().get("error") == -4

    def test_complete_already_cancelled(
        self, click_client, click_config, db: Session, accepted_order
    ):
        """Complete on FAILED Payment → error=-9."""
        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="failed",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="click",
            provider_transaction_id="1004",
        )
        db.add(payment)
        db.flush()

        body = self._complete_body(payment.id, click_trans_id=1004)
        sign_time = "20240101 120100"
        sign = _click_sign_complete(
            1004, _CLICK_SERVICE_ID, _CLICK_SECRET_KEY,
            payment.id, payment.id, _CLICK_AMOUNT_SOUMS, 1, sign_time,
        )
        body["sign_string"] = sign
        resp = click_client.post("/api/payments/click", json=body)
        assert resp.status_code == 200
        assert resp.json().get("error") == -9


# ─────────────────────────────────────────────────────────────────────────────
# E. SANDBOX PROVIDER
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxProvider:

    def test_sandbox_pay_flow(
        self, sandbox_client, db: Session, accepted_order, payme_config,
        monkeypatch,
    ):
        """Sandbox: pay action → Payment→PAID, Order.paid_at set."""
        import config
        monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")

        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="sandbox",
        )
        db.add(payment)
        db.flush()

        resp = sandbox_client.post("/api/payments/sandbox/callback", json={
            "payment_id": payment.id,
            "action": "pay",
            "amount": _ORDER_AMOUNT_TIYINS,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "paid"

        db.expire_all()
        p = db.query(Payment).filter(Payment.id == payment.id).first()
        assert p.status == "paid"

    def test_sandbox_fail_flow(
        self, sandbox_client, db: Session, accepted_order, payme_config,
        monkeypatch,
    ):
        """Sandbox: fail action → Payment→FAILED."""
        import config
        monkeypatch.setattr(config.settings, "ENVIRONMENT", "development")

        payment = Payment(
            order_id=accepted_order.id,
            restaurant_id=accepted_order.restaurant_id,
            status="pending",
            amount=_ORDER_AMOUNT_TIYINS,
            currency="UZS",
            provider="sandbox",
        )
        db.add(payment)
        db.flush()

        resp = sandbox_client.post("/api/payments/sandbox/callback", json={
            "payment_id": payment.id,
            "action": "fail",
            "amount": _ORDER_AMOUNT_TIYINS,
        })
        assert resp.status_code == 200
        db.expire_all()
        p = db.query(Payment).filter(Payment.id == payment.id).first()
        assert p.status == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# F. CONCURRENCY — PostgreSQL only
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.postgres
class TestConcurrency:
    """
    Concurrency tests that verify PostgreSQL-level constraints.

    These tests intentionally bypass the conftest db fixture (which uses
    SAVEPOINT/rollback isolation) because they need multiple independent
    SessionLocal() connections to test real concurrent DB behaviour.

    Each test creates its own data with a real commit() and cleans up
    in a finally block. This is the only correct approach for concurrency
    tests that spawn threads with separate DB sessions.
    """

    def _create_test_data(self):
        """
        Create Agency → Restaurant → Location → Order in the real DB
        (committed, visible to other connections) and return their IDs.
        Caller is responsible for cleanup via _cleanup_test_data().
        """
        from database import SessionLocal
        from auth import hash_password, encrypt_token
        from cryptography.fernet import Fernet
        import os

        fernet_key = os.environ["FERNET_KEY"]
        fernet = Fernet(fernet_key.encode())
        encrypted_token = fernet.encrypt(b"1234567890:AAFakeConcurrencyToken").decode()

        db = SessionLocal()
        try:
            # Use millisecond timestamp for uniqueness across concurrent test runs
            import time as _t
            _ts = int(_t.time() * 1000)

            # Agency
            agency = Agency(
                name=f"Concurrency Agency {_ts}",
                owner_email=f"concurrency_{_ts}@test.uz",
                owner_password_hash=hash_password("testpass"),
            )
            db.add(agency)
            db.flush()

            # Restaurant — slug must be globally unique
            restaurant = Restaurant(
                agency_id=agency.id,
                name=f"Concurrency Restaurant {_ts}",
                slug=f"concurrency-{_ts}",
                admin_password_hash=hash_password("testpass"),
                primary_color="#000000",
                secondary_color="#FFFFFF",
                accent_color="#FF0000",
                telegram_bot_token_encrypted=encrypted_token,
                telegram_dispatcher_id=_ts % 100000000,
                currency="UZS",
            )
            db.add(restaurant)
            db.flush()

            # Location — slug is NOT NULL UNIQUE, required by DB constraint
            import time as _time
            _loc_slug = f"concurrency-loc-{int(_time.time() * 1000)}"
            location = Location(
                restaurant_id=restaurant.id,
                name="Test Location",
                slug=_loc_slug,
                address="Test Address",
                currency="UZS",
                is_active=True,
            )
            db.add(location)
            db.flush()

            # Order in accepted status
            order = Order(
                restaurant_id=restaurant.id,
                location_id=location.id,
                client_name="Concurrency Test Customer",
                client_phone="+998901234567",
                order_type="takeaway",
                total_amount=_ORDER_AMOUNT_TIYINS,
                currency="UZS",
                status="accepted",
            )
            db.add(order)
            db.flush()

            db.commit()
            return {
                "agency_id": agency.id,
                "restaurant_id": restaurant.id,
                "location_id": location.id,
                "order_id": order.id,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _create_payme_config(self, restaurant_id):
        """Create RestaurantPaymentConfig with real commit."""
        from database import SessionLocal
        from auth import encrypt_token

        db = SessionLocal()
        try:
            cfg = RestaurantPaymentConfig(
                restaurant_id=restaurant_id,
                provider="payme",
                merchant_id=_PAYME_MERCHANT_ID,
                service_id=None,
                encrypted_secret=encrypt_token(_PAYME_MERCHANT_KEY),
                is_active=True,
            )
            db.add(cfg)
            db.commit()
            return cfg.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _cleanup_test_data(self, ids):
        """Remove all test data created by _create_test_data()."""
        from database import SessionLocal
        from models.payments import Payment as Pmt, RestaurantPaymentConfig as RPC
        from models.orders import Order as Ord

        db = SessionLocal()
        try:
            if ids.get("order_id"):
                db.query(Pmt).filter(Pmt.order_id == ids["order_id"]).delete()
            if ids.get("restaurant_id"):
                db.query(RPC).filter(RPC.restaurant_id == ids["restaurant_id"]).delete()
            if ids.get("order_id"):
                db.query(Ord).filter(Ord.id == ids["order_id"]).delete()
            if ids.get("location_id"):
                from models.tenant import Location as Loc
                db.query(Loc).filter(Loc.id == ids["location_id"]).delete()
            if ids.get("restaurant_id"):
                from models.tenant import Restaurant as Rest
                db.query(Rest).filter(Rest.id == ids["restaurant_id"]).delete()
            if ids.get("agency_id"):
                from models.tenant import Agency as Ag
                db.query(Ag).filter(Ag.id == ids["agency_id"]).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def test_concurrent_payment_creation_same_order(self):
        """
        Two concurrent Payment creations for same Order.
        Only one should succeed; the second should get 409.
        Protected by: uq_payments_order_active partial unique index.

        Creates real committed data visible to all sessions, then spawns
        two threads each opening an independent SessionLocal connection.
        """
        from database import SessionLocal
        from modules.payments.service import create_payment
        from fastapi import HTTPException

        ids = self._create_test_data()
        self._create_payme_config(ids["restaurant_id"])
        order_id = ids["order_id"]
        restaurant_id = ids["restaurant_id"]

        results = []
        errors = []

        def create_attempt():
            db = SessionLocal()
            try:
                payment, _ = create_payment(
                    db=db,
                    order_id=order_id,
                    provider="payme",
                    restaurant_id=restaurant_id,
                    idempotency_key=None,
                )
                results.append(payment.id)
            except HTTPException as exc:
                errors.append(exc.status_code)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                db.close()

        t1 = threading.Thread(target=create_attempt)
        t2 = threading.Thread(target=create_attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        try:
            # Exactly one success, one conflict (409 HTTP or DB IntegrityError).
            total = len(results) + len(errors)
            assert total == 2, f"Expected 2 total outcomes: results={results} errors={errors}"
            assert len(results) == 1, f"Expected exactly 1 success, got: {results}"
            assert len(errors) == 1, f"Expected exactly 1 conflict, got: {errors}"
        finally:
            self._cleanup_test_data(ids)

    def test_concurrent_payme_perform_transaction(self):
        """
        Two simultaneous Payme PerformTransaction callbacks.
        Only one should transition to PAID; second should be idempotent.

        Creates real committed Payment in PROCESSING state, then fires
        two concurrent PerformTransaction calls from separate DB sessions.
        """
        from database import SessionLocal
        from modules.payments.service import _payme_perform_transaction
        from modules.payments.providers import get_provider
        from modules.payments.providers.base import ParsedCallback, CALLBACK_METHOD_PERFORM

        ids = self._create_test_data()
        self._create_payme_config(ids["restaurant_id"])
        order_id = ids["order_id"]
        restaurant_id = ids["restaurant_id"]

        # Create Payment in PROCESSING state with a real commit.
        db_setup = SessionLocal()
        try:
            payment = Payment(
                order_id=order_id,
                restaurant_id=restaurant_id,
                status="processing",
                amount=_ORDER_AMOUNT_TIYINS,
                currency="UZS",
                provider="payme",
                provider_transaction_id="concurrent_tx_001",
            )
            db_setup.add(payment)
            db_setup.commit()
            payment_id = payment.id
        except Exception:
            db_setup.rollback()
            self._cleanup_test_data(ids)
            raise
        finally:
            db_setup.close()

        results = []
        adapter = get_provider("payme")
        callback = ParsedCallback(
            provider="payme",
            method=CALLBACK_METHOD_PERFORM,
            payment_ref="",
            amount_tiyins=_ORDER_AMOUNT_TIYINS,
            provider_transaction_id="concurrent_tx_001",
        )

        def perform():
            db = SessionLocal()
            try:
                result = _payme_perform_transaction(
                    db, adapter, callback, restaurant_id
                )
                results.append(result)
            finally:
                db.close()

        t1 = threading.Thread(target=perform)
        t2 = threading.Thread(target=perform)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        try:
            # Both threads must return state:2 (PAID):
            # one performs the actual transition, the other hits idempotent path.
            assert len(results) == 2, f"Expected 2 results, got {len(results)}"
            states = [r.get("result", {}).get("state") for r in results]
            assert all(s == 2 for s in states), f"Expected both state=2, got: {states}"

            # Verify exactly one PAID in DB — no double transition.
            db_check = SessionLocal()
            try:
                p = db_check.query(Payment).filter(Payment.id == payment_id).first()
                assert p is not None, "Payment not found after concurrent perform"
                assert p.status == "paid", f"Expected paid, got: {p.status}"
            finally:
                db_check.close()
        finally:
            self._cleanup_test_data(ids)


# ─────────────────────────────────────────────────────────────────────────────
# G. SECURITY
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurity:

    def test_encrypted_secret_not_in_payment_response(
        self, payment_client, accepted_order, payme_config
    ):
        """Payment response never exposes encrypted_secret or merchant_key."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 201
        text = resp.text
        assert "encrypted_secret" not in text
        assert _PAYME_MERCHANT_KEY not in text
        assert "merchant_key" not in text

    def test_checkout_url_contains_payment_id(
        self, payment_client, accepted_order, payme_config
    ):
        """Payme checkout URL encodes payment_id correctly."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "payme",
        })
        assert resp.status_code == 201
        data = resp.json()
        url = data["checkout_url"]
        encoded_part = url.split("/")[-1]
        decoded = base64.b64decode(encoded_part).decode()
        assert f"ac.order_id={data['payment_id']}" in decoded
        assert f"a={_ORDER_AMOUNT_TIYINS}" in decoded

    def test_click_checkout_url_format(
        self, payment_client, accepted_order, click_config
    ):
        """Click checkout URL has correct parameters."""
        resp = payment_client.post("/api/payments/", json={
            "order_id": accepted_order.id,
            "provider": "click",
        })
        assert resp.status_code == 201
        url = resp.json()["checkout_url"]
        assert f"service_id={_CLICK_SERVICE_ID}" in url
        assert f"merchant_id={_CLICK_MERCHANT_ID}" in url
        assert f"transaction_param={resp.json()['payment_id']}" in url

    def test_click_amount_decimal_conversion(self):
        """Click float soums → integer tiyins uses Decimal, not float."""
        from modules.payments.providers.click import ClickProvider
        adapter = ClickProvider()
        # 10000.00 soums = 1_000_000 tiyins
        assert adapter.soums_to_tiyins(10000.0) == 1_000_000
        # Edge case: 0.01 soums = 1 tiyin (not 0 from float error)
        assert adapter.soums_to_tiyins(0.01) == 1
        # 1500.0 soums = 150000 tiyins
        assert adapter.soums_to_tiyins(1500.0) == 150_000
