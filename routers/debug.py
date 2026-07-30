"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ СРАЗУ после получения результата.

Защита: секретный параметр в URL.
Endpoint: GET /api/debug/db-check?secret=taomly_debug_2026
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter(prefix="/api/debug", tags=["debug"])

_SECRET = "taomly_debug_2026"


@router.get("/db-check")
def db_check(
    secret: str = Query(...),
    db: Session = Depends(get_db),
):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    meta = db.execute(text("""
        SELECT
            current_database()                        AS db_name,
            current_schema()                          AS schema_name,
            version()                                 AS pg_version,
            now()::text                               AS server_time,
            inet_server_addr()::text                  AS server_addr,
            inet_server_port()::text                  AS server_port
    """)).mappings().one()

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
            "server_time": meta["server_time"],
            "server_addr": meta["server_addr"],
            "server_port": meta["server_port"],
        },
        "product_id_15": dict(product) if product else None,
    }
