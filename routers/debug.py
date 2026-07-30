"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ СРАЗУ после получения результата.

Защита: требует superadmin JWT токен (Bearer).
Endpoint: GET /api/debug/db-check
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from routers.superadmin import get_current_superadmin

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/db-check")
def db_check(
    request: Request,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_superadmin),
):
    """
    Диагностика: проверяет к какой БД подключено приложение
    и что оно видит в products WHERE id = 15.
    Требует superadmin JWT.
    """
    # 1. Мета-информация о подключении
    meta = db.execute(text("""
        SELECT
            current_database()                        AS db_name,
            current_schema()                          AS schema_name,
            version()                                 AS pg_version,
            current_setting('application_name', true) AS app_name,
            now()::text                               AS server_time,
            inet_server_addr()::text                  AS server_addr,
            inet_server_port()::text                  AS server_port
    """)).mappings().one()

    # 2. Прямой SQL — минуя ORM и joinedload
    product = db.execute(text("""
        SELECT id, name, photo_url
        FROM products
        WHERE id = 15
    """)).mappings().first()

    return {
        "connection": {
            "db_name":    meta["db_name"],
            "schema":     meta["schema_name"],
            "pg_version": meta["pg_version"][:60],
            "app_name":   meta["app_name"],
            "server_time": meta["server_time"],
            "server_addr": meta["server_addr"],
            "server_port": meta["server_port"],
        },
        "product_id_15": dict(product) if product else None,
    }
