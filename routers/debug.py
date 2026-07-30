"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ после завершения диагностики.

Endpoint: GET /api/debug/db-check?secret=taomly_debug_2026
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text

from database import get_db
from models import Category

router = APIRouter(prefix="/api/debug", tags=["debug"])

_SECRET = "taomly_debug_2026"


@router.get("/db-check")
def db_check(
    secret: str = Query(...),
    db: Session = Depends(get_db),
):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    # 1. Прямой SQL — эталон
    raw = db.execute(text(
        "SELECT id, name, photo_url FROM products WHERE id = 15"
    )).mappings().first()

    # 2. ORM через joinedload — точно так же как GET /api/restaurants/chinar
    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == 1)
        .options(joinedload(Category.products))
        .all()
    )
    orm_product = None
    for cat in categories:
        for p in cat.products:
            if p.id == 15:
                orm_product = {
                    "id": p.id,
                    "name": p.name,
                    "photo_url": p.photo_url,
                }
                break

    # 3. Мета
    meta = db.execute(text("""
        SELECT current_database() AS db_name, now()::text AS server_time
    """)).mappings().one()

    return {
        "connection": {
            "db_name": meta["db_name"],
            "server_time": meta["server_time"],
        },
        "raw_sql": dict(raw) if raw else None,
        "orm_joinedload": orm_product,
    }
