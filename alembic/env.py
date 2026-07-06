"""
alembic/env.py — Taomly Platform

Подключает Alembic к нашим моделям и конфигурации.
Поддерживает как online (с живым подключением к БД),
так и offline режим (генерация SQL без подключения).
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Добавляем корень проекта в sys.path чтобы импортировать наши модули
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base  # noqa: E402
import models  # noqa: E402, F401 — импортируем чтобы модели зарегистрировались в Base.metadata

# Alembic Config object — доступ к alembic.ini
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные наших моделей — Alembic использует их для autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    """
    DATABASE_URL берём из env, а не из alembic.ini.
    Это позволяет использовать разные БД для dev/staging/production.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Export it or add to .env before running alembic."
        )
    # SQLAlchemy 2.x требует postgresql://, не postgres://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def run_migrations_offline() -> None:
    """
    Offline mode: генерирует SQL-скрипт без подключения к БД.
    Полезно для ревью миграций перед применением.
    Usage: alembic upgrade head --sql > migration.sql
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # обнаруживать изменения типов колонок
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Online mode: применяет миграции к живой БД.
    Usage: alembic upgrade head
    """
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # NullPool для миграций — не нужен connection pool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

