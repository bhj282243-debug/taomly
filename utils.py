"""
utils.py — Taomly Platform

Общие утилиты, используемые в нескольких роутерах.
Ранее _pg_advisory_lock дублировался в orders.py и billing.py (F-32).

format_price(amount, currency) — единый форматтер цен для backend.
  Используется в routers/orders.py и handlers.py вместо hardcoded 'so\'m'.
  Поддерживаемые валюты: UZS, KZT, RUB, USD, TRY, AED.
  Дефолт: UZS (обратная совместимость для ресторанов без явно заданной валюты).

is_within_schedule(available_from, available_until, tz_str) → bool  [Phase 3]
  Единственная реализация schedule logic для всего проекта.
  Используется в: routers/menu.py, routers/restaurants.py, routers/orders.py.
  Никаких копий этой логики не существует.
"""

import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


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


# ──────────────────────────────────────────
# SCHEDULE HELPER  (Phase 3)
# ──────────────────────────────────────────

def is_within_schedule(
    available_from: datetime.time | None,
    available_until: datetime.time | None,
    tz_str: str,
) -> bool:
    """
    Определяет, находится ли текущий момент внутри окна доступности.

    Это единственная реализация schedule logic в проекте.
    Используется в routers/menu.py, routers/restaurants.py, routers/orders.py.

    Правила:
      NULL / NULL             → True  (нет расписания, всегда доступно)
      from == until           → True  (24 часа, всегда доступно)
      from < until            → нормальное окно: from <= current < until
      from > until (overnight)→ current >= from OR current < until

    Граница:
      available_from  — включается (>=)
      available_until — не включается (<)  [half-open interval]

    Timezone:
      tz_str — IANA timezone string из Location.timezone.
      Текущее время вычисляется в этой timezone.
      Никакого fallback — вызывающий код несёт ответственность за валидный tz_str.

    Raises:
      Не поднимает исключений — при невалидном tz_str возвращает True с WARNING
      (безопасная сторона для расписания: продукт без валидной tz показывается,
      но Order engine обработает это отдельно — там tz всегда из Location).
    """
    # NULL/NULL → нет расписания → всегда доступно
    if available_from is None and available_until is None:
        return True

    # Получаем текущее локальное время ресторана
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        logger.warning(
            "is_within_schedule: невалидный timezone %r — schedule не применяется",
            tz_str,
        )
        return True

    now_local = datetime.datetime.now(tz=tz).time().replace(second=0, microsecond=0)

    # Если одно поле NULL а другое нет — некорректное состояние данных.
    # Fail safe: считаем доступным (не наказываем клиента за баг данных).
    if available_from is None or available_until is None:
        logger.warning(
            "is_within_schedule: одно поле расписания NULL, другое NOT NULL — некорректные данные, "
            "считаем доступным. from=%r until=%r",
            available_from, available_until,
        )
        return True

    # from == until → 24 часа, всегда доступно
    if available_from == available_until:
        return True

    # Нормальное окно: from < until → [from, until)
    if available_from < available_until:
        return available_from <= now_local < available_until

    # Overnight: from > until → например 22:00–02:00
    # Доступно если: current >= from (вечер) ИЛИ current < until (ранее утро)
    return now_local >= available_from or now_local < available_until


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
