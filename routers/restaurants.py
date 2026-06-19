from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import Restaurant, Category, Product

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])


@router.get("/{slug}")
def get_restaurant_by_slug(slug: str, db: Session = Depends(get_db)):
    restaurant = (
        db.query(Restaurant)
        .filter(Restaurant.slug == slug, Restaurant.is_active == True)
        .first()
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant.id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "slug": restaurant.slug,
        "description": restaurant.description,
        "phone": restaurant.phone,
        "address": restaurant.address,
        "is_waiter_call_enabled": restaurant.is_waiter_call_enabled,
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "sort_order": cat.sort_order,
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "price": p.price,
                        "photo_url": p.photo_url,
                        "is_available": p.is_available,
                        "sort_order": p.sort_order,
                    }
                    for p in sorted(cat.products, key=lambda x: x.sort_order)
                    if p.is_available
                ],
            }
            for cat in categories
        ],
    }
