"""
utils.py — Taomly Platform

Общие утилиты, используемые в нескольких роутерах.
Ранее _pg_advisory_lock дублировался в orders.py и billing.py (F-32).
"""

from sqlalchemy.orm import Session
from sqlalchemy import text


def pg_advisory_lock(db: Session, lock_key: int) -> bool:
    """
    Пытается захватить pg_try_advisory_xact_lock для lock_key.

    Возвращает True если блокировка захвачена (или на SQLite — всегда True).
    Возвращает False если блокировка уже удерживается другим запросом.

    Используется для защиты от race condition при создании заказов
    и проверке квот биллинга.
    """
    try:
        row = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": lock_key},
        ).fetchone()
        return bool(row[0]) if row else True
    except Exception:
        # SQLite или другой движок без advisory locks — пропускаем
        return True
