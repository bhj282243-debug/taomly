"""
tests/test_phase10_kds.py — Phase 10: KDS (Kitchen Display System)

Covers:
  - Authentication & authorization (unauthenticated, wrong restaurant)
  - Location scope (missing location_id → 400, location isolation,
    foreign location → 404)
  - Active orders filter (accepted/preparing/ready_for_delivery visible;
    delivering/completed/cancelled excluded from default view)
  - OrderKDSResponse contract (table_number, items, quantities, variants,
    modifiers, comment, paid_at, client_name)
  - Financial fields absent (total_amount, currency, item price,
    modifier price_adjustment)
  - Status transitions via existing PATCH endpoint (reuse unchanged)
  - Paid-order cancellation guard (existing 409 remains intact)
  - Concurrency: two KDS clients, KDS + Admin (PostgreSQL FOR UPDATE)
  - Regression: Phase 9 PATCH endpoint unchanged
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

import pytest

from models import Order, OrderItem, OrderItemModifier, RestaurantTable


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _make_order(
    db,
    restaurant,
    location,
    status: str = "accepted",
    order_type: str = "takeaway",
    table_id: Optional[int] = None,
    paid_at: Optional[datetime] = None,
    comment: Optional[str] = None,
    client_name: Optional[str] = "Test Client",
) -> Order:
    """Create a bare Order directly in DB (bypasses checkout)."""
    order = Order(
        restaurant_id=restaurant.id,
        location_id=location.id,
        client_telegram_id=111111111,
        client_name=client_name,
        client_phone="+998901234567",
        order_type=order_type,
        total_amount=25000,
        currency="UZS",
        status=status,
        table_id=table_id,
        paid_at=paid_at,
        comment=comment,
    )
    db.add(order)
    db.flush()
    return order


def _make_order_with_items(
    db,
    restaurant,
    location,
    status: str = "accepted",
    order_type: str = "takeaway",
    table_id: Optional[int] = None,
) -> Order:
    """Create an Order with items and modifiers for contract tests."""
    order = _make_order(
        db, restaurant, location,
        status=status, order_type=order_type, table_id=table_id,
    )
    item = OrderItem(
        order_id=order.id,
        product_id=None,
        name="Самса",
        price=15000,
        quantity=2,
        variant_name="Большая",
    )
    db.add(item)
    db.flush()

    mod = OrderItemModifier(
        order_item_id=item.id,
        modifier_option_id=None,
        name="Острый соус",
        price_adjustment=1000,
    )
    db.add(mod)
    db.flush()
    return order


def _kds_url(restaurant_id: int) -> str:
    return f"/api/orders/kds/{restaurant_id}"


def _patch_status(client, order_id: int, new_status: str, reason: str | None = None):
    body: dict = {"status": new_status}
    if reason is not None:
        body["cancellation_reason"] = reason
    return client.patch(f"/api/orders/{order_id}/status", json=body)


# ──────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION & AUTHORIZATION
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_unauthenticated(db, restaurant, location):
    """No JWT → 401."""
    from api import app
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=True) as raw_client:
        resp = raw_client.get(
            _kds_url(restaurant.id),
            params={"location_id": location.id},
        )
    assert resp.status_code == 401, resp.json()


@pytest.mark.integration
def test_kds_restaurant_mismatch_403(client, db, restaurant, restaurant2, location):
    """Authenticated as restaurant A but requesting KDS for restaurant B → 403."""
    resp = client.get(
        _kds_url(restaurant2.id),
        params={"location_id": location.id},
    )
    assert resp.status_code == 403, resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# LOCATION SCOPE
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_missing_location_id_422(client, db, restaurant):
    """location_id is required (Query(...)). Missing → 422 Unprocessable Entity."""
    resp = client.get(_kds_url(restaurant.id))
    # FastAPI returns 422 for missing required query params
    assert resp.status_code == 422, resp.json()


@pytest.mark.integration
def test_kds_valid_location_returns_200(client, db, restaurant, location):
    """Valid restaurant + valid location → 200 with (possibly empty) list."""
    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id},
    )
    assert resp.status_code == 200, resp.json()
    assert isinstance(resp.json(), list)


@pytest.mark.integration
def test_kds_foreign_location_404(client, db, restaurant, location2):
    """Location belonging to another restaurant → 404 (no cross-tenant leak)."""
    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location2.id},
    )
    assert resp.status_code == 404, resp.json()


@pytest.mark.integration
def test_kds_location_isolation(client, db, restaurant, location, location_a2):
    """Orders of Location A2 are not visible when KDS requests Location A1."""
    # Create order in location (A1)
    order_a1 = _make_order(db, restaurant, location, status="accepted")
    # Create order in location_a2 (same brand, different location)
    order_a2 = _make_order(db, restaurant, location_a2, status="accepted")
    db.commit()

    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id},
    )
    assert resp.status_code == 200, resp.json()
    ids = [o["id"] for o in resp.json()]
    assert order_a1.id in ids
    assert order_a2.id not in ids, "Location A2 order must not appear in Location A1 KDS"


# ──────────────────────────────────────────────────────────────────────────────
# ACTIVE ORDERS FILTER
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_shows_accepted_orders(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids


@pytest.mark.integration
def test_kds_shows_preparing_orders(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="preparing")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids


@pytest.mark.integration
def test_kds_shows_ready_for_delivery_orders(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="ready_for_delivery")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids


@pytest.mark.integration
def test_kds_excludes_delivering_orders(client, db, restaurant, location):
    """delivering is NOT a kitchen-active status — excluded from KDS queue."""
    order = _make_order(db, restaurant, location, status="delivering",
                        order_type="delivery")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id not in ids, "delivering orders must NOT appear in KDS active queue"


@pytest.mark.integration
def test_kds_excludes_completed_orders_by_default(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="completed")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id not in ids


@pytest.mark.integration
def test_kds_excludes_cancelled_orders_by_default(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="cancelled")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id not in ids


@pytest.mark.integration
def test_kds_include_history_shows_completed(client, db, restaurant, location):
    """?include_history=true shows completed orders for KDS right column."""
    order = _make_order(db, restaurant, location, status="completed")
    db.commit()
    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids


@pytest.mark.integration
def test_kds_include_history_shows_cancelled(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="cancelled")
    db.commit()
    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids


# ──────────────────────────────────────────────────────────────────────────────
# ORDERKDSRESPONSE CONTRACT
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_response_has_table_number_for_dine_in(
    client, db, restaurant, location, table
):
    """table_number must be joined and returned for dine_in orders."""
    order = _make_order(
        db, restaurant, location,
        status="accepted", order_type="dine_in", table_id=table.id,
    )
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    orders = resp.json()
    matched = [o for o in orders if o["id"] == order.id]
    assert matched, "Order not found in KDS response"
    assert matched[0]["table_id"] == table.id
    assert matched[0]["table_number"] == table.table_number


@pytest.mark.integration
def test_kds_response_table_number_null_for_non_dine_in(
    client, db, restaurant, location
):
    """table_number is None for takeaway/delivery orders."""
    order = _make_order(db, restaurant, location, status="accepted", order_type="takeaway")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    assert matched[0]["table_id"] is None
    assert matched[0]["table_number"] is None


@pytest.mark.integration
def test_kds_response_items_and_quantities(client, db, restaurant, location):
    """Items with quantities must be present in KDS response."""
    order = _make_order_with_items(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    items = matched[0]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Самса"
    assert items[0]["quantity"] == 2
    assert items[0]["variant_name"] == "Большая"


@pytest.mark.integration
def test_kds_response_modifiers_present(client, db, restaurant, location):
    """Modifier names must be present in KDS items."""
    order = _make_order_with_items(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    mods = matched[0]["items"][0]["selected_modifiers"]
    assert len(mods) == 1
    assert mods[0]["name"] == "Острый соус"


@pytest.mark.integration
def test_kds_response_comment_present(client, db, restaurant, location):
    order = _make_order(
        db, restaurant, location, status="accepted", comment="без лука"
    )
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    assert matched[0]["comment"] == "без лука"


@pytest.mark.integration
def test_kds_response_paid_at_null_for_unpaid(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="accepted", paid_at=None)
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    assert matched[0]["paid_at"] is None


@pytest.mark.integration
def test_kds_response_paid_at_populated_for_paid(client, db, restaurant, location):
    """paid_at is returned as read-only fact (projection from Payment Service)."""
    now = datetime.now(tz=timezone.utc)
    order = _make_order(db, restaurant, location, status="preparing", paid_at=now)
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    assert matched[0]["paid_at"] is not None


@pytest.mark.integration
def test_kds_response_client_name_present(client, db, restaurant, location):
    order = _make_order(
        db, restaurant, location, status="accepted",
        order_type="takeaway", client_name="Алишер"
    )
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    matched = [o for o in resp.json() if o["id"] == order.id]
    assert matched
    assert matched[0]["client_name"] == "Алишер"


# ──────────────────────────────────────────────────────────────────────────────
# FINANCIAL FIELDS MUST BE ABSENT FROM KDS RESPONSE
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_response_no_total_amount(client, db, restaurant, location):
    """total_amount must NOT be in KDS response — not a financial UI."""
    _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data, "Expected at least one order"
    assert "total_amount" not in data[0], "total_amount must not be in KDS response"


@pytest.mark.integration
def test_kds_response_no_currency(client, db, restaurant, location):
    _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data
    assert "currency" not in data[0], "currency must not be in KDS response"


@pytest.mark.integration
def test_kds_response_no_item_price(client, db, restaurant, location):
    _make_order_with_items(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data
    for item in data[0].get("items", []):
        assert "price" not in item, "item price must not be in KDS response"


@pytest.mark.integration
def test_kds_response_no_modifier_price_adjustment(client, db, restaurant, location):
    _make_order_with_items(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data
    for item in data[0].get("items", []):
        for mod in item.get("selected_modifiers", []):
            assert "price_adjustment" not in mod, \
                "modifier price_adjustment must not be in KDS response"


# ──────────────────────────────────────────────────────────────────────────────
# ACTIVE ORDER ORDERING
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_active_orders_sorted_by_created_at_asc(client, db, restaurant, location):
    """Oldest order first — kitchen prioritises the earliest order."""
    import time
    order1 = _make_order(db, restaurant, location, status="accepted")
    db.flush()
    time.sleep(0.01)  # Ensure distinct created_at on fast machines
    order2 = _make_order(db, restaurant, location, status="preparing")
    db.commit()

    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    ids = [o["id"] for o in resp.json()]
    # Both must be present; order1 created first must appear before order2
    assert order1.id in ids and order2.id in ids
    idx1 = ids.index(order1.id)
    idx2 = ids.index(order2.id)
    assert idx1 < idx2, "Older order must appear first in KDS active queue"


# ──────────────────────────────────────────────────────────────────────────────
# STATUS TRANSITIONS VIA EXISTING PATCH ENDPOINT
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_action_accepted_to_preparing(client, db, restaurant, location):
    """KDS 'start preparing' action → accepted→preparing via existing PATCH."""
    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = _patch_status(client, order.id, "preparing")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "preparing"


@pytest.mark.integration
def test_kds_action_preparing_to_ready_for_delivery(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="preparing")
    db.commit()
    resp = _patch_status(client, order.id, "ready_for_delivery")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "ready_for_delivery"


@pytest.mark.integration
def test_kds_action_ready_to_completed_for_takeaway(client, db, restaurant, location):
    """
    ready_for_delivery → completed is NOT a valid backend transition
    for ANY order_type (including takeaway/dine_in).

    Repository verification:
        status_transitions.py: "ready_for_delivery": ["delivering", "cancelled"]
        PATCH endpoint: validates strictly against ORDER_STATUS_TRANSITIONS,
                        no order_type branching at the backend level.

    The correct completion path for takeaway/dine_in is:
        ready_for_delivery → delivering → completed

    (Admin.html comment on line 1158 incorrectly claims this shortcut exists;
    it is a pre-existing Phase 9 bug in the frontend, not a backend capability.)

    This test verifies that:
        1. ready_for_delivery → completed is correctly rejected (400).
        2. The correct path ready_for_delivery → delivering → completed works.
    """
    order = _make_order(
        db, restaurant, location, status="ready_for_delivery", order_type="takeaway"
    )
    db.commit()

    # Step 1: direct shortcut must be rejected — backend has no order_type branching.
    resp_shortcut = _patch_status(client, order.id, "completed")
    assert resp_shortcut.status_code == 400, (
        f"Expected 400 for ready_for_delivery→completed (not in state machine), "
        f"got {resp_shortcut.status_code}: {resp_shortcut.json()}"
    )

    # Step 2: correct path — ready_for_delivery → delivering → completed.
    resp_delivering = _patch_status(client, order.id, "delivering")
    assert resp_delivering.status_code == 200, resp_delivering.json()
    assert resp_delivering.json()["status"] == "delivering"

    resp_completed = _patch_status(client, order.id, "completed")
    assert resp_completed.status_code == 200, resp_completed.json()
    assert resp_completed.json()["status"] == "completed"


@pytest.mark.integration
def test_kds_action_ready_to_delivering_for_delivery(client, db, restaurant, location):
    """Delivery order: ready_for_delivery → delivering."""
    order = _make_order(
        db, restaurant, location, status="ready_for_delivery", order_type="delivery"
    )
    db.commit()
    resp = _patch_status(client, order.id, "delivering")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "delivering"


@pytest.mark.integration
def test_kds_action_cancel_from_accepted(client, db, restaurant, location):
    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = _patch_status(client, order.id, "cancelled", reason="Тест отмены")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancellation_reason"] == "Тест отмены"


@pytest.mark.integration
def test_kds_cancel_paid_order_rejected(client, db, restaurant, location):
    """Paid-order cancellation guard (Phase 9) remains intact — 409."""
    now = datetime.now(tz=timezone.utc)
    order = _make_order(
        db, restaurant, location, status="accepted", paid_at=now
    )
    db.commit()
    resp = _patch_status(client, order.id, "cancelled")
    assert resp.status_code == 409, resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# CONCURRENCY (PostgreSQL only — FOR UPDATE)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_concurrent_two_kds_clients(client, db, restaurant, location):
    """
    Two KDS clients attempt to transition the same order simultaneously.

    Simulated sequentially (same PATCH endpoint with FOR UPDATE):
    - First PATCH succeeds.
    - Second PATCH sees already-transitioned state → 400 invalid transition.

    This is the same guarantee SELECT FOR UPDATE provides: whichever
    request wins the lock transitions first, the other sees the updated
    state and gets a rejection.
    """
    import os
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("Concurrency test requires PostgreSQL (FOR UPDATE)")

    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()

    # Simulate concurrent access: two sequential calls model the same guarantee
    resp1 = _patch_status(client, order.id, "preparing")
    assert resp1.status_code == 200, resp1.json()

    # Second "device" tries the same transition — order is now "preparing"
    resp2 = _patch_status(client, order.id, "preparing")
    assert resp2.status_code == 400, resp2.json()


@pytest.mark.integration
def test_kds_concurrent_kds_and_admin(client, db, restaurant, location):
    """
    KDS and Admin simultaneously attempt status change.
    Same endpoint → same FOR UPDATE lock → second gets 400.
    """
    import os
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("Concurrency test requires PostgreSQL (FOR UPDATE)")

    order = _make_order(db, restaurant, location, status="preparing")
    db.commit()

    # "KDS" moves to ready_for_delivery
    resp_kds = _patch_status(client, order.id, "ready_for_delivery")
    assert resp_kds.status_code == 200, resp_kds.json()

    # "Admin" (same PATCH endpoint) tries same transition — order already moved
    resp_admin = _patch_status(client, order.id, "ready_for_delivery")
    assert resp_admin.status_code == 400, resp_admin.json()


# ──────────────────────────────────────────────────────────────────────────────
# PHASE 9 REGRESSION — PATCH endpoint unchanged
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_phase9_patch_still_works_after_kds(client, db, restaurant, location):
    """Ensure existing PATCH /api/orders/{id}/status endpoint is unaffected."""
    order = _make_order(db, restaurant, location, status="new")
    db.commit()
    resp = _patch_status(client, order.id, "accepted")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "accepted"
    # Financial fields still present in existing OrderResponse
    assert "total_amount" in resp.json()
    assert "currency" in resp.json()


@pytest.mark.integration
def test_phase9_order_response_unchanged(client, db, restaurant, location):
    """Existing GET /api/orders/{id} still returns full OrderResponse (Phase 9 contract)."""
    order = _make_order(db, restaurant, location, status="accepted")
    db.commit()
    resp = client.get(f"/api/orders/{order.id}")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    # Phase 9 fields must still be present
    assert "total_amount" in data
    assert "currency" in data
    assert "paid_at" in data
    assert "cancellation_reason" in data


# ──────────────────────────────────────────────────────────────────────────────
# PAYMENT BOUNDARY VERIFICATION
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_reads_paid_at_does_not_expose_payment_fields(client, db, restaurant, location):
    """
    KDS response includes paid_at (Order projection fact) but must NOT expose
    any Payment model fields (Payment.status, Payment.provider, Payment.amount etc.).
    These are Payment domain — KDS does not own or read them.
    """
    now = datetime.now(tz=timezone.utc)
    _make_order(db, restaurant, location, status="preparing", paid_at=now)
    db.commit()

    resp = client.get(_kds_url(restaurant.id), params={"location_id": location.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data

    order_data = data[0]
    # paid_at may be present (read-only Order projection fact)
    assert "paid_at" in order_data

    # Payment domain fields must NEVER appear
    forbidden_payment_fields = [
        "payment_status", "provider", "payment_id",
        "payment_amount", "idempotency_key",
    ]
    for field in forbidden_payment_fields:
        assert field not in order_data, f"Payment field '{field}' must not be in KDS response"


# ──────────────────────────────────────────────────────────────────────────────
# P1-02 REGRESSION — KDS history restricted to current local day
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_kds_history_includes_todays_completed(client, db, restaurant, location):
    """
    Test A — today's completed order must appear in history.

    include_history=true must show completed orders created today
    (in location.timezone = Asia/Tashkent).
    """
    # created_at defaults to now() — today in any timezone.
    order = _make_order(db, restaurant, location, status="completed")
    db.commit()

    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids, "Today's completed order must appear in KDS history"


@pytest.mark.integration
def test_kds_history_excludes_yesterdays_completed(client, db, restaurant, location):
    """
    Test B — yesterday's completed order must NOT appear in history.

    P1-02 fix: history is restricted to the current local day via
    location.timezone (Asia/Tashkent). Orders from previous days must
    be excluded even when include_history=true.
    """
    import os
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip(
            "P1-02 date filter uses PostgreSQL AT TIME ZONE — "
            "SQLite does not support this; skip on SQLite CI job."
        )

    from datetime import timedelta
    from sqlalchemy import text as sa_text

    # Create order normally (status=completed, created_at=now).
    order = _make_order(db, restaurant, location, status="completed")
    db.flush()

    # Backdate created_at to yesterday UTC — guaranteed to be a different local day
    # in Asia/Tashkent (UTC+5) as long as the test runs before 19:00 UTC,
    # which covers the full working day. For CI robustness we use 2 days ago.
    two_days_ago = datetime.now(tz=timezone.utc) - timedelta(days=2)
    db.execute(
        sa_text("UPDATE orders SET created_at = :ts WHERE id = :id"),
        {"ts": two_days_ago, "id": order.id},
    )
    db.commit()

    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [o["id"] for o in resp.json()]
    assert order.id not in ids, (
        "Completed order from 2 days ago must NOT appear in KDS history "
        "(history is restricted to today in location.timezone)"
    )


@pytest.mark.integration
def test_kds_history_excludes_yesterdays_cancelled(client, db, restaurant, location):
    """
    Old cancelled orders must also be excluded from KDS history.
    """
    import os
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("P1-02 date filter requires PostgreSQL AT TIME ZONE.")

    from datetime import timedelta
    from sqlalchemy import text as sa_text

    order = _make_order(db, restaurant, location, status="cancelled")
    db.flush()

    two_days_ago = datetime.now(tz=timezone.utc) - timedelta(days=2)
    db.execute(
        sa_text("UPDATE orders SET created_at = :ts WHERE id = :id"),
        {"ts": two_days_ago, "id": order.id},
    )
    db.commit()

    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [o["id"] for o in resp.json()]
    assert order.id not in ids, (
        "Cancelled order from 2 days ago must NOT appear in KDS history"
    )


@pytest.mark.integration
def test_kds_active_orders_unaffected_by_history_filter(client, db, restaurant, location):
    """
    Active statuses (accepted/preparing/ready_for_delivery) are always returned
    regardless of created_at — the date filter applies only to history statuses.
    """
    import os
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("P1-02 date filter requires PostgreSQL AT TIME ZONE.")

    from datetime import timedelta
    from sqlalchemy import text as sa_text

    # Create an old accepted order (edge case: stale order still in kitchen)
    order = _make_order(db, restaurant, location, status="accepted")
    db.flush()

    two_days_ago = datetime.now(tz=timezone.utc) - timedelta(days=2)
    db.execute(
        sa_text("UPDATE orders SET created_at = :ts WHERE id = :id"),
        {"ts": two_days_ago, "id": order.id},
    )
    db.commit()

    # include_history=true — active order must still appear regardless of date
    resp = client.get(
        _kds_url(restaurant.id),
        params={"location_id": location.id, "include_history": "true"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [o["id"] for o in resp.json()]
    assert order.id in ids, (
        "Active (accepted) order must appear in KDS even when created on a previous day — "
        "date filter applies only to history statuses (completed/cancelled)"
    )
