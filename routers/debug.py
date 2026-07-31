"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ после завершения диагностики.
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text

from database import get_db
from models import Category, Restaurant

router = APIRouter(prefix="/api/debug", tags=["debug"])

_SECRET = "taomly_debug_2026"


@router.get("/db-check")
def db_check(
    secret: str = Query(...),
    db: Session = Depends(get_db),
):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    # 1. Прямой SQL
    raw = db.execute(text(
        "SELECT id, name, photo_url FROM products WHERE id = 15"
    )).mappings().first()

    # 2. ORM joinedload
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

    # 3. Точная копия кода из GET /api/restaurants/chinar
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == "chinar",
        Restaurant.is_active == True,
    ).first()

    slug_categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    ) if restaurant else []

    slug_product = None
    all_products_in_cat1 = []
    for cat in slug_categories:
        for p in sorted(cat.products, key=lambda x: x.sort_order):
            if cat.id == 1:
                all_products_in_cat1.append({
                    "id": p.id,
                    "name": p.name,
                    "photo_url": p.photo_url,
                    "is_available": p.is_available,
                })
            if p.id == 15:
                slug_product = {
                    "id": p.id,
                    "name": p.name,
                    "photo_url": p.photo_url,
                    "is_available": p.is_available,
                    "category_id": cat.id,
                }

    return {
        "raw_sql": dict(raw) if raw else None,
        "orm_joinedload": orm_product,
        "slug_endpoint_product": slug_product,
        "all_products_in_category_1": all_products_in_cat1,
    }


@router.get("/img-test")
def img_test(secret: str = Query(...)):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    from fastapi.responses import HTMLResponse
    html = """<!DOCTYPE html>
<html>
<head>
<meta http-equiv="Content-Security-Policy" content="img-src *; default-src * 'unsafe-inline'">
</head>
<body style="background:#fff;padding:20px">
<h3>IMG Test</h3>
<p>Test 1 — прямой URL:</p>
<img id="img1" src="https://pub-c0be929a95b747a8afca1075f2a80505.r2.dev/restaurants/1/31ad38f794b242ffabd137915df8dd72.png" 
     style="max-width:300px;border:2px solid red"
     onload="document.getElementById('r1').textContent='✅ ЗАГРУЗИЛОСЬ'"
     onerror="document.getElementById('r1').textContent='❌ ОШИБКА'">
<p id="r1" style="color:gray">загружается...</p>
</body>
</html>"""
    response = HTMLResponse(content=html)
    # Переопределяем CSP middleware для этого endpoint
    response.headers["Content-Security-Policy"] = "img-src * data: blob:; default-src * 'unsafe-inline'"
    return response
