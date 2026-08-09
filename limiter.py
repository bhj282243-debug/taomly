"""
limiter.py — slowapi Limiter singleton.
Выделен из api.py чтобы избежать циклических импортов.

F-12: default_limits применяет RATE_LIMIT_API ко всем endpoint'ам автоматически.
F-13: RATE_LIMIT_API теперь реально используется из config.py.
Точечные @limiter.limit() декораторы (логины, заказы) имеют более строгие
лимиты и перекрывают default для своих endpoint'ов.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_API],
)
