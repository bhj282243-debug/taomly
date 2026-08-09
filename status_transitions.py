"""
status_transitions.py — Taomly Platform

Единственный источник правды для допустимых переходов статусов.
Ранее каждый роутер (orders, reservations, waiter_calls) содержал
свою копию словаря — M-4 / F-33.

Импортировать:
    from status_transitions import (
        ORDER_STATUS_TRANSITIONS,
        RESERVATION_STATUS_TRANSITIONS,
        WAITER_CALL_STATUS_TRANSITIONS,
    )
"""

# Заказы (orders)
ORDER_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "new":                ["accepted", "cancelled"],
    "accepted":           ["preparing", "cancelled"],
    "preparing":          ["ready_for_delivery", "cancelled"],
    "ready_for_delivery": ["delivering", "cancelled"],
    "delivering":         ["completed"],
    "completed":          [],
    "cancelled":          [],
}

# Бронирования (reservations)
RESERVATION_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "new":       ["confirmed", "cancelled"],
    "confirmed": ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}

# Вызовы официанта (waiter_calls)
WAITER_CALL_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "active":    ["accepted", "cancelled"],
    "accepted":  ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}
