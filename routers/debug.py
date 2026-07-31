"""
ВРЕМЕННЫЙ диагностический роутер.
УДАЛИТЬ после завершения диагностики.
"""
import httpx
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
    restaurant = db.query(Restaurant).filter(
        Restaurant.slug == "chinar", Restaurant.is_active == True
    ).first()
    slug_categories = (
        db.query(Category).filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products)).order_by(Category.sort_order).all()
    ) if restaurant else []
    slug_product = None
    for cat in slug_categories:
        for p in sorted(cat.products, key=lambda x: x.sort_order):
            if p.id == 15:
                slug_product = {"id": p.id, "name": p.name, "photo_url": p.photo_url}
    meta = db.execute(text(
        "SELECT current_database() AS db_name, now()::text AS server_time"
    )).mappings().one()
    return {
        "connection": {"db_name": meta["db_name"], "server_time": meta["server_time"]},
        "raw_sql": dict(raw) if raw else None,
        "orm_joinedload": orm_product,
        "slug_endpoint_product": slug_product,
    }


@router.get("/r2-headers")
async def r2_headers(secret: str = Query(...)):
    """
    Делает HEAD запрос к R2 с сервера (не из браузера).
    Возвращает HTTP статус и все заголовки ответа.
    Также делает запрос с Referer заголовком чтобы проверить блокировку.
    """
    if secret != _SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    results = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # 1. HEAD запрос без Referer
        try:
            r = await client.head(_R2_URL, timeout=10)
            results["no_referer"] = {
                "status": r.status_code,
                "headers": dict(r.headers),
            }
        except Exception as e:
            results["no_referer"] = {"error": str(e)}

        # 2. HEAD запрос с Referer taomly.onrender.com
        try:
            r = await client.head(
                _R2_URL,
                headers={"Referer": "https://taomly.onrender.com/"},
                timeout=10,
            )
            results["with_referer"] = {
                "status": r.status_code,
                "headers": dict(r.headers),
            }
        except Exception as e:
            results["with_referer"] = {"error": str(e)}

        # 3. GET запрос с Origin заголовком (как браузер при CORS)
        try:
            r = await client.get(
                _R2_URL,
                headers={
                    "Origin": "https://taomly.onrender.com",
                    "Referer": "https://taomly.onrender.com/",
                },
                timeout=10,
            )
            results["with_origin"] = {
                "status": r.status_code,
                "content_type": r.headers.get("content-type"),
                "cors_header": r.headers.get("access-control-allow-origin"),
                "corp_header": r.headers.get("cross-origin-resource-policy"),
                "coep_header": r.headers.get("cross-origin-embedder-policy"),
                "content_length": r.headers.get("content-length"),
            }
        except Exception as e:
            results["with_origin"] = {"error": str(e)}

    return {"r2_url": _R2_URL, "results": results}


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
fetch("{_R2_URL}", {{method: "HEAD", mode: "cors"}})
  .then(r => {{
    document.getElementById('fetch_result').textContent =
      '✅ fetch OK: status=' + r.status + ' content-type=' + (r.headers.get('content-type')||'н/д');
  }})
  .catch(e => {{
    document.getElementById('fetch_result').textContent = '❌ fetch ОШИБКА: ' + e.toString();
  }});
const metas = document.querySelectorAll('meta[http-equiv]');
const cspMeta = Array.from(metas).find(m => m.getAttribute('http-equiv').toLowerCase() === 'content-security-policy');
document.getElementById('csp_result').textContent = cspMeta ? cspMeta.getAttribute('content') : 'meta CSP не найден (CSP из HTTP заголовка)';
</script>
</body>
</html>"""
    response = HTMLResponse(content=html)
    response.headers["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval'; img-src * data: blob:; connect-src *"
    return response
