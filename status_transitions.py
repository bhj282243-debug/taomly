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

# Бронирования (reservations) — Phase 15, Slice B (PHASE_15_SLICE_B ... SPEC v2, §9).
#
# Единственная таблица переходов. Список допустимых СТАТУСОВ (CHECK в БД) и таблица
# ПЕРЕХОДОВ — разные вещи: наличие значения в CHECK не разрешает из него любой переход.
#
#   requested -> confirmed | cancelled
#   confirmed -> seated | completed | cancelled | no_show
#   seated    -> completed
#   completed, cancelled, no_show — терминальные (переходов нет)
#
# Только данные: файл ничего не пишет в БД и не ставит timestamps.
_RESERVATION_STATUS_TRANSITIONS_CORE: dict[str, list[str]] = {
    "requested": ["confirmed", "cancelled"],
    "confirmed": ["seated", "completed", "cancelled", "no_show"],
    "seated":    ["completed"],
    "completed": [],
    "cancelled": [],
    "no_show":   [],
}

# R1 (transitional): legacy-статус 'new' — прежнее имя 'requested'. Это НЕ полноценный
# статус жизненного цикла, а временный alias для броней, созданных до перехода.
# Ведёт себя как 'requested' (те же допустимые переходы), в 'requested' не переходит.
# Удаляется в R2 вместе с миграцией 0033 (new -> requested в данных, final CHECK).
LEGACY_STATUS_ALIASES: dict[str, str] = {"new": "requested"}

RESERVATION_STATUS_TRANSITIONS: dict[str, list[str]] = {
    **_RESERVATION_STATUS_TRANSITIONS_CORE,
    **{
        legacy: list(_RESERVATION_STATUS_TRANSITIONS_CORE[canonical])
        for legacy, canonical in LEGACY_STATUS_ALIASES.items()
    },
}

# Вызовы официанта (waiter_calls)
WAITER_CALL_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "active":    ["accepted", "cancelled"],
    "accepted":  ["completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}
