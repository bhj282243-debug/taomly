"""
modules/scheduled/activation.py — Taomly Platform
Phase 14: Scheduled Order Activation Background Loop.

ARCHITECTURE (BLOCK-02 Resolution — Option A: per-location prep time):

Activation formula:
  activation_time = scheduled_at
                  - location.preparation_time_minutes (NULL → 0)
                  - zone.eta_minutes (delivery only, NULL → 0)
                  - ACTIVATION_BUFFER_MINUTES

  Order activates when: now() >= activation_time
  i.e.: scheduled_at <= now() + prep_time + zone_eta + BUFFER

scheduled_at semantics (confirmed from Architecture Spec):
  = time at which customer wants to RECEIVE the order
  (pickup: time to collect; delivery: time of arrival; dine_in: service start)

For DELIVERY: eta_minutes from delivery_zone is included in activation formula
because scheduled_at is the delivery arrival time, not kitchen start time.
For PICKUP/DINE_IN: only preparation_time + BUFFER used.

NULL fallbacks (documented in migration 0028):
  preparation_time_minutes NULL → 0 (no prep time configured)
  zone.eta_minutes NULL          → 0 (no ETA configured)

Deployment: single asyncio task inside FastAPI lifespan (--workers 1).
Multi-worker protection: SELECT FOR UPDATE SKIP LOCKED per activation
batch — correct even if future multi-worker deployment occurs.

Activation does NOT use raw UPDATE — uses PATCH /status domain logic
equivalent (status transition validation + notify_client_accepted).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS: int = 60
ACTIVATION_BUFFER_MINUTES: int = 5


def activate_scheduled_orders(
    db: Session,
    notify_fn: Callable,
) -> int:
    """
    Activates scheduled orders whose activation window has arrived.
    Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent execution.

    Returns number of orders activated.

    notify_fn signature: notify_fn(order, restaurant, location) -> None
    Called synchronously (no BackgroundTasks in loop context).
    """
    from models.orders import Order
    from models.delivery_zones import DeliveryZone
    from models.tenant import Location, Restaurant
    from status_transitions import VALID_STATUS_TRANSITIONS

    now_utc = datetime.now(timezone.utc)
    activated = 0

    # Find candidates: scheduled orders in 'new' status whose
    # activation time <= now. Use SKIP LOCKED for safe polling.
    candidates = (
        db.query(Order)
        .filter(
            Order.scheduled_at.isnot(None),
            Order.status == "new",
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    for order in candidates:
        try:
            # Load location for prep_time and timezone.
            location = db.query(Location).filter(
                Location.id == order.location_id,
            ).first()
            if not location:
                logger.warning(
                    "activation: location not found for order %s — skipping",
                    order.id,
                )
                continue

            prep_time = location.preparation_time_minutes or 0
            zone_eta = 0

            # For delivery: include zone.eta_minutes in activation formula.
            if order.order_type == "delivery" and order.delivery_zone_id:
                zone = db.query(DeliveryZone).filter(
                    DeliveryZone.id == order.delivery_zone_id,
                ).first()
                if zone and zone.eta_minutes:
                    zone_eta = zone.eta_minutes

            # activation_time = scheduled_at - prep_time - zone_eta - BUFFER
            activation_time = order.scheduled_at - timedelta(
                minutes=prep_time + zone_eta + ACTIVATION_BUFFER_MINUTES
            )

            if now_utc < activation_time:
                # Not yet time to activate.
                continue

            # Validate transition (use domain logic, not raw UPDATE).
            allowed = VALID_STATUS_TRANSITIONS.get(order.status, [])
            if "accepted" not in allowed:
                logger.warning(
                    "activation: order %s status=%s cannot transition to accepted — skipping",
                    order.id, order.status,
                )
                continue

            order.status = "accepted"
            db.flush()

            # Load restaurant for notification.
            restaurant = db.query(Restaurant).filter(
                Restaurant.id == order.restaurant_id,
            ).first()

            db.commit()
            activated += 1

            logger.info(
                "activation: order %s activated (scheduled_at=%s prep=%dm eta=%dm)",
                order.id, order.scheduled_at, prep_time, zone_eta,
            )

            # Notify client: "your order is now being prepared".
            # Called synchronously (no BackgroundTasks in loop context).
            if restaurant and location:
                try:
                    notify_fn(order, restaurant, location)
                except Exception:
                    logger.exception(
                        "activation: notify_client_accepted failed for order %s", order.id,
                    )

        except Exception:
            db.rollback()
            logger.exception("activation: error processing order %s — rolled back", order.id)

    return activated


async def run_scheduled_activation_loop(
    get_db_fn: Callable,
    notify_fn: Callable,
) -> None:
    """
    Async loop that polls for scheduled orders to activate.
    Runs inside FastAPI lifespan — cancelled cleanly on shutdown.

    get_db_fn: generator function (same as database.get_db)
    notify_fn: handlers.notify_client_accepted
    """
    logger.info(
        "Scheduled activation loop started (interval=%ds, buffer=%dm)",
        POLL_INTERVAL_SECONDS,
        ACTIVATION_BUFFER_MINUTES,
    )
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            db: Session = next(get_db_fn())
            try:
                count = activate_scheduled_orders(db, notify_fn)
                if count:
                    logger.info("activation: activated %d scheduled order(s)", count)
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Scheduled activation loop cancelled — shutting down cleanly")
            break
        except Exception:
            logger.exception("Scheduled activation loop error — continuing after next sleep")
