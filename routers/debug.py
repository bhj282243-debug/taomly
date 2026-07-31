"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ после завершения диагностики.
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text

from database import get_db
from models import Category, Restaurant

router = APIRouter(prefix="/api/debug", tags=["debug"])

_SECRET = "taomly_debug_2026"
_R2_URL = "https://pub-c0be929a95b747a8afca1075f2a80505.r2.dev/restaurants/1/31ad38f794b242ffabd137915df8dd72.png"


@router.get("/db-check")
def db_check(secret: str = Query(...), db: Session = Depends(get_db)):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    raw = db.execute(text("SELECT id, name, photo_url FROM products WHERE id = 15")).mappings().first()
    categories = (
        db.query(Category).filter(Category.restaurant_id == 1)
        .options(joinedload(Category.products)).all()
    )
    orm_product = None
    for cat in categories:
        for p in cat.products:
            if p.id == 15:
                orm_product = {"id": p.id, "name": p.name, "photo_url": p.photo_url}
    restaurant = db.query(Restaurant).filter(Restaurant.slug == "chinar", Restaurant.is_active == True).first()
    slug_categories = (
        db.query(Category).filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products)).order_by(Category.sort_order).all()
    ) if restaurant else []
    slug_product = None
    for cat in slug_categories:
        for p in sorted(cat.products, key=lambda x: x.sort_order):
            if p.id == 15:
                slug_product = {"id": p.id, "name": p.name, "photo_url": p.photo_url}
    meta = db.execute(text("SELECT current_database() AS db_name, now()::text AS server_time")).mappings().one()
    return {
        "connection": {"db_name": meta["db_name"], "server_time": meta["server_time"]},
        "raw_sql": dict(raw) if raw else None,
        "orm_joinedload": orm_product,
        "slug_endpoint_product": slug_product,
    }


@router.get("/img-test")
def img_test(secret: str = Query(...)):
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>IMG Debug</title></head>
<body style="font-family:monospace;padding:20px;background:#fff">
<h3>IMG + Fetch Debug</h3>

<p><b>1. img тег:</b></p>
<img id="img1" src="{_R2_URL}"
     style="max-width:200px;border:2px solid red;display:block"
     onload="document.getElementById('img_result').textContent='✅ IMG загрузилось'"
     onerror="document.getElementById('img_result').textContent='❌ IMG ОШИБКА'">
<p id="img_result">загружается...</p>

<p><b>2. fetch HEAD запрос:</b></p>
<p id="fetch_result">выполняется...</p>

<p><b>3. CSP заголовок страницы:</b></p>
<p id="csp_result">проверяется...</p>

<script>
// Проверяем fetch
fetch("{_R2_URL}", {{method: "HEAD", mode: "cors"}})
  .then(r => {{
    document.getElementById('fetch_result').textContent = 
      '✅ fetch OK: status=' + r.status + ' content-type=' + (r.headers.get('content-type')||'н/д');
  }})
  .catch(e => {{
    document.getElementById('fetch_result').textContent = '❌ fetch ОШИБКА: ' + e.toString();
  }});

// Проверяем CSP
const metas = document.querySelectorAll('meta[http-equiv]');
const cspMeta = Array.from(metas).find(m => m.getAttribute('http-equiv').toLowerCase() === 'content-security-policy');
document.getElementById('csp_result').textContent = cspMeta ? cspMeta.getAttribute('content') : 'meta CSP не найден (CSP из HTTP заголовка)';
</script>
</body>
</html>"""
    response = HTMLResponse(content=html)
    response.headers["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval'; img-src * data: blob:; connect-src *"
    return response
