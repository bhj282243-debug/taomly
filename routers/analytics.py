"""
routers/analytics.py — Taomly Analytics Dashboard
SQL-only аналитика для ресторанного администратора.

Endpoints:
  GET /api/analytics/summary         — KPI: выручка, заказы, средний чек, повторные клиенты
  GET /api/analytics/revenue-by-day  — выручка по дням (для line-chart)
  GET /api/analytics/top-dishes      — топ-10 блюд по продажам
  GET /api/analytics/peak-hours      — часы пик (0–23)
  GET /api/analytics/order-types     — разбивка по типу заказа

Query param: ?period=today|7d|30d|90d|this_month  (default: 30d)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List

from auth import get_current_restaurant_admin
from database import get_db
from models import Restaurant
from schemas import (
    SummaryResponse,
    DayRevenueItem,
    DishItem,
    HourItem,
    OrderTypeItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _period_to_dates(period: str) -> tuple[datetime, datetime]:
    now = datetime.now(tz=timezone.utc)

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "30d":
        start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "90d":
        start = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимый период. Допустимые: today, 7d, 30d, 90d, this_month",
        )

    return start, now


# ──────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────

@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    period: str = Query("30d"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> SummaryResponse:
    """KPI-сводка: выручка, заказы, средний чек, клиенты."""
    start, end = _period_to_dates(period)

    sql = text("""
        SELECT
            COALESCE(SUM(CASE WHEN status = 'completed' THEN total_amount ELSE 0 END), 0)   AS revenue,
            COUNT(*)                                                                           AS orders_total,
            COUNT(*) FILTER (WHERE status = 'completed')                                      AS orders_completed,
            COUNT(*) FILTER (WHERE status = 'cancelled')                                      AS orders_cancelled,
            COALESCE(
                AVG(total_amount) FILTER (WHERE status = 'completed'), 0
            )::BIGINT                                                                          AS avg_check
        FROM orders
        WHERE restaurant_id = :rid
          AND created_at >= :start
          AND created_at <  :end
    """)
    row = db.execute(sql, {"rid": restaurant.id, "start": start, "end": end}).fetchone()

    sql_clients = text("""
        SELECT
            COUNT(*) FILTER (WHERE cnt >= 2) AS returning_clients,
            COUNT(*) FILTER (WHERE cnt  = 1) AS new_clients
        FROM (
            SELECT client_telegram_id, COUNT(*) AS cnt
            FROM orders
            WHERE restaurant_id       = :rid
              AND created_at         >= :start
              AND created_at         <  :end
              AND client_telegram_id IS NOT NULL
            GROUP BY client_telegram_id
        ) sub
    """)
    row_c = db.execute(sql_clients, {"rid": restaurant.id, "start": start, "end": end}).fetchone()

    return SummaryResponse(
        period=period,
        revenue=int(row.revenue),
        orders_total=int(row.orders_total),
        orders_completed=int(row.orders_completed),
        orders_cancelled=int(row.orders_cancelled),
        avg_check=int(row.avg_check),
        returning_clients=int(row_c.returning_clients) if row_c else 0,
        new_clients=int(row_c.new_clients) if row_c else 0,
    )


@router.get("/revenue-by-day", response_model=List[DayRevenueItem])
def get_revenue_by_day(
    period: str = Query("30d"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> List[DayRevenueItem]:
    """Выручка и количество заказов по дням (для графика)."""
    start, end = _period_to_dates(period)

    sql = text("""
        SELECT
            DATE(created_at AT TIME ZONE 'UTC')                                       AS day,
            COALESCE(SUM(CASE WHEN status = 'completed' THEN total_amount ELSE 0 END), 0) AS revenue,
            COUNT(*)                                                                   AS orders
        FROM orders
        WHERE restaurant_id = :rid
          AND created_at   >= :start
          AND created_at   <  :end
        GROUP BY day
        ORDER BY day ASC
    """)
    rows = db.execute(sql, {"rid": restaurant.id, "start": start, "end": end}).fetchall()

    return [
        DayRevenueItem(
            date=str(r.day),
            revenue=int(r.revenue),
            orders=int(r.orders),
        )
        for r in rows
    ]


@router.get("/top-dishes", response_model=List[DishItem])
def get_top_dishes(
    period: str = Query("30d"),
    limit: int = Query(10, ge=1, le=20),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> List[DishItem]:
    """Топ-N блюд по количеству продаж (исключая отменённые заказы)."""
    start, end = _period_to_dates(period)

    sql = text("""
        SELECT
            oi.name,
            SUM(oi.quantity)               AS qty,
            SUM(oi.price * oi.quantity)    AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.restaurant_id = :rid
          AND o.created_at   >= :start
          AND o.created_at   <  :end
          AND o.status       != 'cancelled'
        GROUP BY oi.name
        ORDER BY qty DESC
        LIMIT :limit
    """)
    rows = db.execute(sql, {"rid": restaurant.id, "start": start, "end": end, "limit": limit}).fetchall()

    return [
        DishItem(rank=i + 1, name=r.name, qty=int(r.qty), revenue=int(r.revenue))
        for i, r in enumerate(rows)
    ]


@router.get("/peak-hours", response_model=List[HourItem])
def get_peak_hours(
    period: str = Query("30d"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> List[HourItem]:
    """Распределение заказов по часам суток (UTC+5 для Узбекистана)."""
    start, end = _period_to_dates(period)

    sql = text("""
        SELECT
            EXTRACT(HOUR FROM created_at AT TIME ZONE 'Asia/Tashkent')::INT AS hour,
            COUNT(*) AS orders
        FROM orders
        WHERE restaurant_id = :rid
          AND created_at   >= :start
          AND created_at   <  :end
        GROUP BY hour
        ORDER BY hour ASC
    """)
    rows = db.execute(sql, {"rid": restaurant.id, "start": start, "end": end}).fetchall()

    hour_map = {r.hour: int(r.orders) for r in rows}
    return [HourItem(hour=h, orders=hour_map.get(h, 0)) for h in range(24)]


@router.get("/order-types", response_model=List[OrderTypeItem])
def get_order_types(
    period: str = Query("30d"),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> List[OrderTypeItem]:
    """Разбивка заказов по типу (dine_in / delivery / takeaway)."""
    start, end = _period_to_dates(period)

    sql = text("""
        SELECT
            order_type,
            COUNT(*)                                                                        AS orders,
            COALESCE(SUM(CASE WHEN status = 'completed' THEN total_amount ELSE 0 END), 0)  AS revenue
        FROM orders
        WHERE restaurant_id = :rid
          AND created_at   >= :start
          AND created_at   <  :end
        GROUP BY order_type
        ORDER BY orders DESC
    """)
    rows = db.execute(sql, {"rid": restaurant.id, "start": start, "end": end}).fetchall()

    return [
        OrderTypeItem(order_type=r.order_type, orders=int(r.orders), revenue=int(r.revenue))
        for r in rows
    ]
