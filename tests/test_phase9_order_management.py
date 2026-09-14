"""
tests/test_phase9_order_management.py — Phase 9: Order Management

Covers:
  - Valid status transitions (full matrix per state machine)
  - Invalid transitions (HTTP 400)
  - Paid-order cancellation guard (HTTP 409)
  - cancellation_reason: stored, returned, ignored for non-cancel
  - paid_at in OrderResponse
  - modifiers and table_id in OrderResponse
  - Authorization: unauthenticated, wrong-role, wrong-restaurant
  - PostgreSQL concurrency: SELECT FOR UPDATE prevents double transition
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

import pytest

from models import Order, OrderItem


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _make_order(
    db,
    restaurant,
    location,
    status: str = "new",
    paid_at: Optional[datetime] = None,
    order_type: str = "takeaway",
    table_id: Optional[int] = None,
) -> Order:
    """Create a bare Order directly in DB (bypasses checkout)."""
    order = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_telegram_id=111111111,
        client_name="Test Client",
        client_phone="+998901234567",
        order_type=order_type,
        total_amount=15000,
        currency="UZS",
        status=status,
        paid_at=paid_at,
        table_id=table_id,
    )
    db.add(order)
    db.flush()

    item = OrderItem(
        order_id=order.id,
        product_id=None,
        name="Самса",
        price=15000,
        quantity=1,
    )
    db.add(item)
    db.flush()
    return order


def _patch_status(client, order_id: int, new_status: str, reason: str | None = None):
    body: dict = {"status": new_status}
    if reason is not None:
        body["cancellation_reason"] = reason
    return client.patch(f"/api/orders/{order_id}/status", json=body)


# ──────────────────────────────────────────────────────────────────────────────
# VALID TRANSITIONS
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_transition_new_to_accepted(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="new")
    resp = _patch_status(client, order.id, "accepted")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "accepted"


@pytest.mark.integration
def test_transition_new_to_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="new")
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"


@pytest.mark.integration
def test_transition_accepted_to_preparing(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="accepted")
    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "preparing"


@pytest.mark.integration
def test_transition_accepted_to_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="accepted")
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"


@pytest.mark.integration
def test_transition_preparing_to_ready_for_delivery(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="preparing")
    resp = _patch_status(client, order.id, "ready_for_delivery")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "ready_for_delivery"


@pytest.mark.integration
def test_transition_preparing_to_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="preparing")
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"


@pytest.mark.integration
def test_transition_ready_for_delivery_to_delivering(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="ready_for_delivery")
    resp = _patch_status(client, order.id, "delivering")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "delivering"


@pytest.mark.integration
def test_transition_ready_for_delivery_to_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="ready_for_delivery")
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"


@pytest.mark.integration
def test_transition_delivering_to_completed(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="delivering")
    resp = _patch_status(client, order.id, "completed")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "completed"


@pytest.mark.integration
def test_full_delivery_chain(client, db, restaurant, location):
    """new → accepted → preparing → ready_for_delivery → delivering → completed"""
    order = _make_order(db, restaurant, location, status="new", order_type="delivery")
    for target in ["accepted", "preparing", "ready_for_delivery", "delivering", "completed"]:
        resp = _patch_status(client, order.id, target)
        assert resp.status_code == 200, f"Step {target} failed: {resp.json()}"
        assert resp.json()["status"] == target


@pytest.mark.integration
def test_full_takeaway_chain(client, db, restaurant, location):
    """accepted → preparing → ready_for_delivery → delivering → completed
    Backend state machine is the same for all order types.
    ready_for_delivery → completed is NOT a valid transition (must go via delivering).
    The UI getNextStatus() handles the non-delivery shortcut visually,
    but the backend chain is always: ready_for_delivery → delivering → completed.
    """
    order = _make_order(db, restaurant, location, status="accepted", order_type="takeaway")
    for target in ["preparing", "ready_for_delivery", "delivering", "completed"]:
        resp = _patch_status(client, order.id, target)
        assert resp.status_code == 200, f"Step {target} failed: {resp.json()}"
        assert resp.json()["status"] == target


# ──────────────────────────────────────────────────────────────────────────────
# INVALID TRANSITIONS
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_invalid_new_to_completed(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="new")
    resp = _patch_status(client, order.id, "completed")
    assert resp.status_code == 400, resp.json()
    assert "невозможен" in resp.json()["detail"]


@pytest.mark.integration
def test_invalid_completed_to_preparing(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="completed")
    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 400, resp.json()


@pytest.mark.integration
def test_invalid_completed_to_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="completed")
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 400, resp.json()


@pytest.mark.integration
def test_invalid_cancelled_to_accepted(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="cancelled")
    resp = _patch_status(client, order.id, "accepted")
    assert resp.status_code == 400, resp.json()


@pytest.mark.integration
def test_invalid_cancelled_to_preparing(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="cancelled")
    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 400, resp.json()


@pytest.mark.integration
def test_invalid_delivering_to_preparing(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="delivering")
    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 400, resp.json()


@pytest.mark.integration
def test_invalid_ready_for_delivery_to_completed(client, db, restaurant, location):
    """ready_for_delivery → completed is invalid for ALL order types.
    Must always go through delivering first. UI handles display differently,
    but backend machine is identical for delivery and non-delivery."""
    order = _make_order(db, restaurant, location, status="ready_for_delivery")
    resp = _patch_status(client, order.id, "completed")
    assert resp.status_code == 400, resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# CANCELLATION
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_cancel_unpaid_new_no_reason(client, db, restaurant, location):
    """Unpaid order in 'new' can be cancelled without reason."""
    order = _make_order(db, restaurant, location, status="new", paid_at=None)
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["cancellation_reason"] is None


@pytest.mark.integration
def test_cancel_unpaid_accepted_with_reason(client, db, restaurant, location):
    """Unpaid order in 'accepted' can be cancelled with a reason."""
    order = _make_order(db, restaurant, location, status="accepted", paid_at=None)
    resp = _patch_status(client, order.id, "cancelled", reason="Клиент передумал")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["cancellation_reason"] == "Клиент передумал"


@pytest.mark.integration
def test_cancel_unpaid_preparing(client, db, restaurant, location):
    """Unpaid order in 'preparing' can be cancelled."""
    order = _make_order(db, restaurant, location, status="preparing", paid_at=None)
    resp = _patch_status(client, order.id, "cancelled", reason="Нет ингредиентов")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancellation_reason"] == "Нет ингредиентов"


@pytest.mark.integration
def test_cancel_unpaid_ready_for_delivery(client, db, restaurant, location):
    """Unpaid order in 'ready_for_delivery' can be cancelled."""
    order = _make_order(db, restaurant, location, status="ready_for_delivery", paid_at=None)
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"


@pytest.mark.integration
def test_cancel_paid_order_rejected(client, db, restaurant, location):
    """PAID order (paid_at IS NOT NULL) cannot be cancelled — HTTP 409."""
    paid_time = datetime.now(timezone.utc)
    order = _make_order(db, restaurant, location, status="accepted", paid_at=paid_time)
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 409, resp.json()
    detail = resp.json()["detail"]
    assert "оплачен" in detail.lower(), f"Expected 'оплачен' in detail, got: {detail}"


@pytest.mark.integration
def test_cancel_paid_order_rejected_from_new(client, db, restaurant, location):
    """PAID order in 'new' status cannot be cancelled either."""
    paid_time = datetime.now(timezone.utc)
    order = _make_order(db, restaurant, location, status="new", paid_at=paid_time)
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 409, resp.json()


@pytest.mark.integration
def test_cancellation_reason_ignored_for_non_cancel(client, db, restaurant, location):
    """cancellation_reason is silently ignored when status != 'cancelled'."""
    order = _make_order(db, restaurant, location, status="new")
    resp = client.patch(
        f"/api/orders/{order.id}/status",
        json={"status": "accepted", "cancellation_reason": "This should be ignored"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "accepted"
    # cancellation_reason must NOT be stored for non-cancel transitions
    assert data["cancellation_reason"] is None


@pytest.mark.integration
def test_cancellation_reason_persisted_in_db(client, db, restaurant, location):
    """Verify cancellation_reason is actually stored in the database."""
    order = _make_order(db, restaurant, location, status="accepted")
    reason_text = "Ресторан закрылся"
    resp = _patch_status(client, order.id, "cancelled", reason=reason_text)
    assert resp.status_code == 200, resp.json()

    # Query the DB directly to confirm persistence
    db.expire(order)
    db.refresh(order)
    assert order.cancellation_reason == reason_text


@pytest.mark.integration
def test_cancellation_reason_whitespace_stripped(client, db, restaurant, location):
    """cancellation_reason is stripped of leading/trailing whitespace."""
    order = _make_order(db, restaurant, location, status="new")
    resp = _patch_status(client, order.id, "cancelled", reason="  Технический сбой  ")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["cancellation_reason"] == "Технический сбой"


@pytest.mark.integration
def test_cancellation_reason_empty_string_stored_as_null(client, db, restaurant, location):
    """Empty string cancellation_reason is stored as NULL."""
    order = _make_order(db, restaurant, location, status="new")
    resp = _patch_status(client, order.id, "cancelled", reason="")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["cancellation_reason"] is None


# ──────────────────────────────────────────────────────────────────────────────
# RESPONSE FIELDS
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_response_paid_at_null_for_unpaid(client, db, restaurant, location):
    """paid_at is None in OrderResponse for an unpaid order."""
    order = _make_order(db, restaurant, location, status="new", paid_at=None)
    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["paid_at"] is None


@pytest.mark.integration
def test_response_paid_at_populated(client, db, restaurant, location):
    """paid_at is present in OrderResponse when order has been paid."""
    paid_time = datetime.now(timezone.utc)
    order = _make_order(db, restaurant, location, status="accepted", paid_at=paid_time)
    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["paid_at"] is not None
    # Should be parseable as ISO datetime
    parsed = datetime.fromisoformat(data["paid_at"].replace("Z", "+00:00"))
    assert parsed is not None


@pytest.mark.integration
def test_response_cancellation_reason_after_cancel(client, db, restaurant, location):
    """cancellation_reason appears in OrderResponse after cancellation."""
    order = _make_order(db, restaurant, location, status="preparing")
    resp = _patch_status(client, order.id, "cancelled", reason="Нет курьера")
    assert resp.status_code == 200
    assert resp.json()["cancellation_reason"] == "Нет курьера"

    # Verify via GET as well
    get_resp = client.get(f"/api/orders/{order.id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["cancellation_reason"] == "Нет курьера"


@pytest.mark.integration
def test_response_cancellation_reason_null_when_not_set(client, db, restaurant, location):
    """cancellation_reason is None in OrderResponse when not cancelled."""
    order = _make_order(db, restaurant, location, status="new")
    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200
    assert resp.json()["cancellation_reason"] is None


@pytest.mark.integration
def test_response_table_id_for_dine_in(client, db, restaurant, location, table):
    """table_id is returned in OrderResponse for dine_in orders."""
    order = _make_order(
        db, restaurant, location,
        status="new", order_type="dine_in", table_id=table.id,
    )
    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["table_id"] == table.id


@pytest.mark.integration
def test_response_modifiers_returned(client, db, restaurant, location):
    """Modifiers are included in OrderItemResponse.selected_modifiers."""
    from models import ModifierGroup, ModifierOption, OrderItemModifier

    # Create a modifier for the order item
    order = _make_order(db, restaurant, location, status="new")
    order_item = db.query(OrderItem).filter(OrderItem.order_id == order.id).first()

    # Add a modifier directly to the order item
    mod = OrderItemModifier(
        order_item_id=order_item.id,
        modifier_option_id=None,
        name="Острый соус",
        price_adjustment=2000,
    )
    db.add(mod)
    db.flush()

    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert len(data["items"]) >= 1
    item = data["items"][0]
    assert "selected_modifiers" in item
    assert len(item["selected_modifiers"]) == 1
    assert item["selected_modifiers"][0]["name"] == "Острый соус"
    assert item["selected_modifiers"][0]["price_adjustment"] == 2000


# ──────────────────────────────────────────────────────────────────────────────
# AUTHORIZATION
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_patch_status_unauthenticated(db, restaurant, location):
    """Unauthenticated PATCH /status → 401 or 403."""
    from api import app
    from database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    order = _make_order(db, restaurant, location, status="new")

    try:
        from fastapi.testclient import TestClient
        # No auth headers, no dependency override for restaurant_admin
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.patch(
            f"/api/orders/{order.id}/status",
            json={"status": "accepted"},
        )
        # Without valid JWT, should be 401 or 403
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.security
def test_patch_status_wrong_restaurant(client, db, restaurant, restaurant2, location2):
    """Restaurant A cannot change status of Restaurant B's order → 404."""
    # order belongs to restaurant2, but client is authenticated as restaurant
    order = Order(
        restaurant_id=restaurant2.id,
        location_id=location2.id,
        client_telegram_id=999,
        order_type="takeaway",
        total_amount=10000,
        currency="USD",
        status="accepted",
    )
    db.add(order)
    db.flush()

    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.json()}"


@pytest.mark.security
def test_get_order_wrong_restaurant(client, db, restaurant, restaurant2, location2):
    """Restaurant A cannot GET an order belonging to Restaurant B → 404."""
    order = Order(
        restaurant_id=restaurant2.id,
        location_id=location2.id,
        client_telegram_id=999,
        order_type="takeaway",
        total_amount=10000,
        currency="USD",
        status="new",
    )
    db.add(order)
    db.flush()

    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ──────────────────────────────────────────────────────────────────────────────
# CONCURRENCY — PostgreSQL only
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.postgres
@pytest.mark.integration
def test_concurrent_status_transition_for_update(client, db, restaurant, location):
    """
    Two concurrent PATCH requests on the same order in 'accepted' state.

    Request A: accepted → preparing
    Request B: accepted → cancelled

    Expected:
      - exactly one HTTP 200 (successful transition)
      - exactly one HTTP 400 (invalid transition because state already changed)
      - final order status is exactly one of: 'preparing' or 'cancelled'

    Uses SELECT FOR UPDATE — proven by the fact that only one transition wins.
    """
    import os, threading
    from sqlalchemy import text

    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("Concurrency test requires PostgreSQL")

    # Create the order using the existing test db session + fixtures
    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()
    order_id = order.id

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from api import app
    from auth import create_restaurant_token
    from database import get_db

    token = create_restaurant_token(restaurant)
    auth_header = {"Authorization": f"Bearer {token}"}

    results = []
    barrier = threading.Barrier(2)

    def patch_status(target_status: str):
        conc_engine = create_engine(DATABASE_URL)
        ConcSession = sessionmaker(bind=conc_engine)

        def thread_db():
            session = ConcSession()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = thread_db
        c = TestClient(app, raise_server_exceptions=False)
        barrier.wait()
        resp = c.patch(
            f"/api/orders/{order_id}/status",
            json={"status": target_status},
            headers=auth_header,
        )
        results.append(resp.status_code)
        conc_engine.dispose()

    t1 = threading.Thread(target=patch_status, args=("preparing",))
    t2 = threading.Thread(target=patch_status, args=("cancelled",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    app.dependency_overrides.clear()

    assert len(results) == 2, f"Expected 2 results, got {results}"
    assert sorted(results) == [200, 400], (
        f"Expected one 200 and one 400 (FOR UPDATE prevents double transition). Got: {results}"
    )

    # Verify final state
    from sqlalchemy import text as sa_text
    verify_engine = create_engine(DATABASE_URL)
    with verify_engine.connect() as conn:
        row = conn.execute(
            sa_text("SELECT status FROM orders WHERE id = :id"), {"id": order_id}
        ).fetchone()
    verify_engine.dispose()

    assert row[0] in ("preparing", "cancelled"), (
        f"Final status must be 'preparing' or 'cancelled', got: {row[0]}"
    )
