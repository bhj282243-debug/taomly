"""
database.py — Taomly Platform
Синхронный движок SQLAlchemy (совместим с текущими роутерами).

Изменения относительно v1:
  - get_db() делает rollback перед close — устранена утечка транзакций PostgreSQL
  - pool_pre_ping=True — защита от stale connections (Neon закрывает idle)
  - pool_size=3, max_overflow=5 — оптимально для Render Free + Neon Free
  - pool_recycle=1800 — второй уровень защиты от закрытых соединений
  - DATABASE_URL читается из config.settings — единый источник конфигурации

Примечание: переход на AsyncSession запланирован после закрытия
всех дыр безопасности и получения первых клиентов.
"""

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from config import settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# КОНФИГ
# ──────────────────────────────────────────
DATABASE_URL = settings.DATABASE_URL

# ──────────────────────────────────────────
# ДВИЖОК
# ──────────────────────────────────────────
# pool_size / max_overflow / pool_recycle поддерживаются только QueuePool
# (PostgreSQL). SQLite использует SingletonThreadPool и не принимает эти
# аргументы — поэтому для тестов (sqlite:///) передаём минимальный набор.
_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": not _is_sqlite}
if not _is_sqlite:
    # Render Free + Neon Free: держим пул небольшим
    _engine_kwargs["pool_size"] = 3
    _engine_kwargs["max_overflow"] = 5
    # Пересоздаём соединение каждые 30 минут
    _engine_kwargs["pool_recycle"] = 1800

engine = create_engine(DATABASE_URL, **_engine_kwargs)

# ──────────────────────────────────────────
# ФАБРИКА СЕССИЙ
# ──────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ──────────────────────────────────────────
# BASE
# ──────────────────────────────────────────
Base = declarative_base()


# ──────────────────────────────────────────
# DEPENDENCY
# ──────────────────────────────────────────
def get_db() -> Session:
    """
    FastAPI Depends-зависимость.

    Гарантии:
      1. rollback при любом необработанном исключении — устраняет висящие
         транзакции и блокировки строк в PostgreSQL
      2. close всегда вызывается в finally — соединение возвращается в пул
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
