"""
utils.py — Taomly Platform

Общие утилиты, используемые в нескольких роутерах.
Ранее _pg_advisory_lock дублировался в orders.py и billing.py (F-32).

format_price(amount, currency) — единый форматтер цен для backend.
  Используется в routers/orders.py и handlers.py вместо hardcoded 'so\'m'.
  Поддерживаемые валюты: UZS, KZT, RUB, USD, TRY, AED.
  Дефолт: UZS (обратная совместимость для ресторанов без явно заданной валюты).
"""

from sqlalchemy.orm import Session
from sqlalchemy import text


# ──────────────────────────────────────────
# PRICE FORMATTER (backend)
# ──────────────────────────────────────────

# Таблица форматирования: currency_code → (prefix, suffix, decimals)
#   prefix  — символ/код перед суммой (пустая строка если нет)
#   suffix  — символ/строка после суммы (пустая строка если нет)
#   decimals — количество знаков после запятой (0 для целых валют)
_CURRENCY_FMT: dict[str, tuple[str, str, int]] = {
    "UZS": ("",     " so'm", 0),
    "KZT": ("",     " ₸",    0),
    "RUB": ("",     " ₽",    0),
    "USD": ("$",    "",      2),
    "TRY": ("₺",   "",      2),
    "AED": ("AED ", "",      2),
}
_CURRENCY_DEFAULT = "UZS"


def format_price(amount: int | float, currency: str | None = None) -> str:
    """
    Форматирует сумму в строку с символом валюты.

    Примеры:
      format_price(25000, "UZS") → "25 000 so'm"
      format_price(25000, "KZT") → "25 000 ₸"
      format_price(1500,  "RUB") → "1 500 ₽"
      format_price(25,    "USD") → "$25.00"
      format_price(25,    "TRY") → "₺25.00"
      format_price(25,    "AED") → "AED 25.00"

    Разделитель тысяч — пробел (неразрывный в UI не нужен для backend-строк).
    Неизвестная валюта → fallback на UZS (не ломает работу).
    """
    cur = (currency or _CURRENCY_DEFAULT).upper()
    prefix, suffix, decimals = _CURRENCY_FMT.get(cur, _CURRENCY_FMT[_CURRENCY_DEFAULT])

    if decimals == 0:
        # Целые валюты: разделитель тысяч — пробел
        formatted = f"{int(amount):,}".replace(",", "\u00a0")  # неразрывный пробел
    else:
        formatted = f"{float(amount):.{decimals}f}"

    return f"{prefix}{formatted}{suffix}"


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
