"""
schemas/analytics.py — Taomly Platform

Analytics response schemas.
"""

from pydantic import BaseModel


# ──────────────────────────────────────────
# ANALYTICS SCHEMAS
# ──────────────────────────────────────────

class SummaryResponse(BaseModel):
    period: str
    revenue: int
    orders_total: int
    orders_completed: int
    orders_cancelled: int
    avg_check: int
    returning_clients: int
    new_clients: int


class DayRevenueItem(BaseModel):
    date: str
    revenue: int
    orders: int


class DishItem(BaseModel):
    rank: int
    name: str
    qty: int
    revenue: int


class HourItem(BaseModel):
    hour: int
    orders: int


class OrderTypeItem(BaseModel):
    order_type: str
    orders: int
    revenue: int
