"""
routers/menu.py — Taomly Platform

Изменения относительно v1:
  - ProductCreate и ProductUpdate перенесены в schemas.py (были прямо в роутере)
  - GET /{restaurant_id}/all: добавлена JWT-авторизация restaurant_admin +
    tenant-проверка → только свой ресторан
  - POST /product/: добавлена JWT-авторизация + tenant-проверка category →
    нельзя создать продукт в категории чужого ресторана (IDOR закрыт)
  - PATCH /product/{product_id}: добавлена JWT-авторизация + tenant-проверка
    product → нельзя изменить продукт чужого ресторана (IDOR закрыт)
  - DELETE /product/{product_id}: новый эндпоинт с авторизацией и tenant-проверкой
  - Category CRUD: создание/удаление категорий с авторизацией и tenant-проверкой
  - price: валидация gt=0 перенесена в схему (ProductCreate/ProductUpdate в schemas.py)
  - Все сообщения об ошибках унифицированы на русский язык
  - Логирование через logger.exception с контекстом
  - Дублирующийся код get_active_restaurant вынесен в хелпер
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from auth import get_current_restaurant_admin
from database import get_db
from models import Category, Product, Restaurant
from schemas import (
    CategoryResponse,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    CategoryCreate,
    CategoryUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────
# ХЕЛПЕР — получить активный ресторан или 404
# ──────────────────────────────────────────
def _get_active_restaurant(restaurant_id: int, db: Session) -> Restaurant:
    """
    Загружает активный ресторан по ID.
    Используется в публичных эндпоинтах (без JWT).
    """
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == restaurant_id,
        Restaurant.is_active == True,
    ).first()
    if not restaurant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресторан не найден",
        )
    return restaurant


# ──────────────────────────────────────────
# GET /{restaurant_id} — публичное меню (клиент Mini App)
# ──────────────────────────────────────────
@router.get("/{restaurant_id}", response_model=List[CategoryResponse])
def get_menu(restaurant_id: int, db: Session = Depends(get_db)):
    """
    Возвращает публичное меню ресторана — только доступные продукты.
    Авторизация не требуется (публичный эндпоинт для клиентов).
    Пустые категории (без доступных продуктов) не возвращаются.
    """
    _get_active_restaurant(restaurant_id, db)

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    for c in categories:
        c.products = sorted(
            [p for p in (c.products or []) if p.is_available],
            key=lambda p: p.sort_order,
        )

    return [c for c in categories if c.products]


# ──────────────────────────────────────────
# GET /{restaurant_id}/all — полное меню (админка)
# ──────────────────────────────────────────
@router.get("/{restaurant_id}/all", response_model=List[CategoryResponse])
def get_menu_all(
    restaurant_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Возвращает полное меню ресторана включая недоступные продукты.

    Tenant-изоляция: restaurant_id из URL проверяется против токена JWT.
    Ресторан А не может просматривать меню ресторана Б.
    """
    if restaurant.id != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к меню этого ресторана",
        )

    categories = (
        db.query(Category)
        .filter(Category.restaurant_id == restaurant_id)
        .options(joinedload(Category.products))
        .order_by(Category.sort_order)
        .all()
    )

    for c in categories:
        c.products = sorted(c.products or [], key=lambda p: p.sort_order)

    return categories


# ──────────────────────────────────────────
# POST /category/ — создать категорию (админка)
# ──────────────────────────────────────────
@router.post("/category/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт категорию меню.
    Категория автоматически привязывается к ресторану из JWT-токена.
    Клиент не передаёт restaurant_id — tenant-изоляция гарантирована.
    """
    category = Category(
        restaurant_id=restaurant.id,
        name=data.name,
        sort_order=data.sort_order,
    )
    db.add(category)

    try:
        db.commit()
        db.refresh(category)
    except Exception:
        logger.exception(
            "Ошибка при создании категории: restaurant_id=%s name=%s",
            restaurant.id, data.name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании категории",
        )

    logger.info(
        "Категория создана: category_id=%s name=%s restaurant_id=%s",
        category.id, data.name, restaurant.id,
    )
    return category


# ──────────────────────────────────────────
# PATCH /category/{category_id} — обновить категорию (админка)
# ──────────────────────────────────────────
@router.patch("/category/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    data: CategoryUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет категорию меню.
    Tenant-изоляция: категория ищется только среди категорий ресторана из токена.
    """
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.restaurant_id == restaurant.id,
    ).first()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена",
        )

    if data.name is not None:
        category.name = data.name
    if data.sort_order is not None:
        category.sort_order = data.sort_order

    try:
        db.commit()
        db.refresh(category)
    except Exception:
        logger.exception(
            "Ошибка при обновлении категории: category_id=%s", category_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении категории",
        )

    return category


# ──────────────────────────────────────────
# DELETE /category/{category_id} — удалить категорию (админка)
# ──────────────────────────────────────────
@router.delete("/category/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет категорию и все её продукты (cascade в models.py).
    Tenant-изоляция: категория ищется только среди категорий ресторана из токена.
    """
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.restaurant_id == restaurant.id,
    ).first()

    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена",
        )

    try:
        db.delete(category)
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при удалении категории: category_id=%s", category_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении категории",
        )

    logger.info(
        "Категория удалена: category_id=%s restaurant_id=%s",
        category_id, restaurant.id,
    )


# ──────────────────────────────────────────
# POST /product/ — создать продукт (админка)
# ──────────────────────────────────────────
@router.post("/product/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Создаёт продукт в меню.

    Tenant-изоляция: category_id проверяется на принадлежность ресторану
    из JWT-токена. Нельзя создать продукт в категории чужого ресторана.
    price > 0 проверяется в Pydantic схеме (ProductCreate).
    """
    # Tenant-изоляция: категория должна принадлежать этому ресторану
    category = db.query(Category).filter(
        Category.id == data.category_id,
        Category.restaurant_id == restaurant.id,
    ).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Категория не найдена в этом ресторане",
        )

    product = Product(
        restaurant_id=restaurant.id,
        category_id=data.category_id,
        name=data.name,
        price=data.price,
        description=data.description,
        photo_url=data.photo_url,
        is_available=data.is_available,
        sort_order=data.sort_order,
    )
    db.add(product)

    try:
        db.commit()
        db.refresh(product)
    except Exception:
        logger.exception(
            "Ошибка при создании продукта: restaurant_id=%s name=%s",
            restaurant.id, data.name,
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при создании продукта",
        )

    logger.info(
        "Продукт создан: product_id=%s name=%s restaurant_id=%s",
        product.id, data.name, restaurant.id,
    )
    return product


# ──────────────────────────────────────────
# PATCH /product/{product_id} — обновить продукт (админка)
# ──────────────────────────────────────────
@router.patch("/product/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    data: ProductUpdate,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Обновляет продукт.

    Tenant-изоляция: продукт ищется только среди продуктов ресторана из токена.
    Нельзя изменить продукт чужого ресторана зная его ID (IDOR закрыт).
    Если меняется category_id — новая категория тоже проверяется на принадлежность.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    # Если меняем категорию — она тоже должна принадлежать этому ресторану
    if data.category_id is not None:
        category = db.query(Category).filter(
            Category.id == data.category_id,
            Category.restaurant_id == restaurant.id,
        ).first()
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Категория не найдена в этом ресторане",
            )
        product.category_id = data.category_id

    if data.name is not None:
        product.name = data.name
    if data.price is not None:
        product.price = data.price
    if data.description is not None:
        product.description = data.description
    if data.photo_url is not None:
        product.photo_url = data.photo_url
    if data.is_available is not None:
        product.is_available = data.is_available
    if data.sort_order is not None:
        product.sort_order = data.sort_order

    try:
        db.commit()
        db.refresh(product)
    except Exception:
        logger.exception(
            "Ошибка при обновлении продукта: product_id=%s", product_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении продукта",
        )

    logger.info(
        "Продукт обновлён: product_id=%s restaurant_id=%s",
        product_id, restaurant.id,
    )
    return product


# ──────────────────────────────────────────
# DELETE /product/{product_id} — удалить продукт (админка)
# ──────────────────────────────────────────
@router.delete("/product/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    Удаляет продукт из меню.
    Tenant-изоляция: продукт ищется только среди продуктов ресторана из токена.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.restaurant_id == restaurant.id,
    ).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Продукт не найден",
        )

    try:
        db.delete(product)
        db.commit()
    except Exception:
        logger.exception(
            "Ошибка при удалении продукта: product_id=%s", product_id
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при удалении продукта",
        )

    logger.info(
        "Продукт удалён: product_id=%s restaurant_id=%s",
        product_id, restaurant.id,
    )
