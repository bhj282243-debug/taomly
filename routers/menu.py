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

Изменения v2 (Security):
  - is_popular добавлен в create_product и update_product
"""

import io
import logging
import uuid
from typing import List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from auth import get_current_restaurant_admin
from config import settings
from limiter import limiter
from database import get_db
from models import Category, Product, Restaurant, Subscription, SubscriptionPlan, UsageEvent
from sqlalchemy import func
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
# ХЕЛПЕР — проверка лимита продуктов по тарифу
# ──────────────────────────────────────────
def _check_products_quota(db: Session, restaurant_id: int) -> None:
    """
    Проверяет что ресторан не превысил лимит продуктов по текущему тарифному плану.

    Алгоритм:
      1. Загружает активную подписку ресторана.
      2. Если подписки нет — считает ресторан на Free плане.
      3. Если products_limit == -1 — безлимит, проверка не нужна.
      4. Считает все продукты ресторана (включая недоступные).
      5. Если лимит достигнут — возвращает HTTP 402 с понятным сообщением.

    Вызывается в create_product до любой записи в БД.
    """
    sub = (
        db.query(Subscription)
        .filter(
            Subscription.restaurant_id == restaurant_id,
            Subscription.is_active == True,
        )
        .order_by(Subscription.started_at.desc())
        .first()
    )

    if sub is None:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "Free").first()
        if plan is None:
            return
    else:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()
        if plan is None:
            return

    if plan.products_limit == -1:
        return

    count = db.query(func.count(Product.id)).filter(
        Product.restaurant_id == restaurant_id,
    ).scalar() or 0

    if count >= plan.products_limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Лимит продуктов тарифа достигнут ({plan.products_limit}). Обновите тарифный план.",
        )


# ──────────────────────────────────────────
# POST /upload-photo — загрузка фото блюда в Cloudflare R2
# ──────────────────────────────────────────

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _get_s3_client():
    """
    Возвращает boto3 S3-клиент для Cloudflare R2.
    Вызывается при каждом запросе — boto3 сам кэширует соединения внутри.
    Не делаем module-level singleton чтобы изменения ENV вступали в силу без рестарта.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def _delete_r2_photo(photo_url: str) -> None:
    """
    Удаляет объект из R2 по публичному URL.

    Удаляет только файлы из собственного R2-бакета (URL начинается с R2_PUBLIC_URL).
    Внешние URL (например, старые Telegraph-ссылки) игнорируются без ошибки.
    Ошибки удаления логируются, но не пробрасываются — БД уже обновлена.
    """
    if not photo_url:
        return

    public_base = settings.R2_PUBLIC_URL.rstrip("/")
    if not photo_url.startswith(public_base + "/"):
        logger.info("_delete_r2_photo: URL не из нашего R2, пропускаем: %s", photo_url)
        return

    object_key = photo_url[len(public_base) + 1:]
    if not object_key:
        return

    try:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=object_key)
        logger.info("R2 объект удалён: key=%s", object_key)
    except (BotoCoreError, ClientError):
        logger.exception("Не удалось удалить R2 объект: key=%s", object_key)


@router.post("/upload-photo")
@limiter.limit("20/hour")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
):
    """
    Загружает фото блюда в Cloudflare R2.
    Возвращает публичный URL загруженного файла.

    Ограничения:
      - Только JPEG / PNG / WebP / GIF
      - Максимум 5 MB
      - Требует JWT-авторизации ресторанного администратора
      - Rate limit: 20 запросов в час с одного IP (Foundation Task 11.2)
    """
    # Проверяем что R2 настроен
    if not settings.R2_ACCESS_KEY_ID or not settings.R2_ACCOUNT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище фотографий не настроено. Свяжитесь с поддержкой.",
        )

    # Проверка MIME-типа
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Допустимые форматы: JPEG, PNG, WebP, GIF",
        )

    # Читаем файл и проверяем размер
    data = await file.read()
    if len(data) > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Размер файла превышает 5 MB ({len(data) / 1024 / 1024:.1f} MB)",
        )

    # Проверяем реальный формат файла по magic bytes через Pillow.
    # Content-Type из HTTP-заголовка клиент может подделать — здесь мы убеждаемся,
    # что байты действительно являются изображением поддерживаемого формата.
    # .verify() деструктивен (после него объект Image нельзя использовать),
    # но нам нужна только проверка, не дальнейшая обработка.
    try:
        from PIL import Image, UnidentifiedImageError
        img = Image.open(io.BytesIO(data))
        img.verify()
    except (UnidentifiedImageError, Exception):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл не является изображением или повреждён. Допустимые форматы: JPEG, PNG, WebP, GIF",
        )

    # Генерируем уникальное имя: restaurants/<id>/<uuid>.<ext>
    ext = _MIME_TO_EXT[content_type]
    object_key = f"restaurants/{restaurant.id}/{uuid.uuid4().hex}{ext}"

    # Загружаем в R2
    try:
        s3 = _get_s3_client()
        s3.put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception(
            "Ошибка загрузки фото в R2: restaurant_id=%s key=%s",
            restaurant.id, object_key,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ошибка загрузки файла. Попробуйте ещё раз.",
        ) from exc

    public_url = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{object_key}"

    logger.info(
        "Фото загружено: restaurant_id=%s key=%s url=%s",
        restaurant.id, object_key, public_url,
    )

    return {"url": public_url}


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
    _check_products_quota(db, restaurant.id)

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
        is_bestseller=data.is_bestseller,
        is_new=data.is_new,
        is_spicy=data.is_spicy,
        is_chef_choice=data.is_chef_choice,
        is_popular=data.is_popular,
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

    # Записываем событие создания продукта для биллинга/аудита.
    # Ошибка записи не откатывает созданный продукт.
    try:
        db.add(UsageEvent(restaurant_id=restaurant.id, event_type="product_created"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Не удалось записать UsageEvent product_created: product_id=%s restaurant_id=%s",
            product.id, restaurant.id,
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
        old_photo_url = product.photo_url  # запоминаем до изменения
        product.photo_url = data.photo_url
    else:
        old_photo_url = None
    if data.is_available is not None:
        product.is_available = data.is_available
    if data.sort_order is not None:
        product.sort_order = data.sort_order
    if data.is_bestseller is not None:
        product.is_bestseller = data.is_bestseller
    if data.is_new is not None:
        product.is_new = data.is_new
    if data.is_spicy is not None:
        product.is_spicy = data.is_spicy
    if data.is_chef_choice is not None:
        product.is_chef_choice = data.is_chef_choice
    if data.is_popular is not None:
        product.is_popular = data.is_popular

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

    # Удаляем старое фото из R2 после успешного commit
    # Удаляем только если фото реально сменилось и старый URL отличается от нового
    if old_photo_url and old_photo_url != product.photo_url:
        _delete_r2_photo(old_photo_url)

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

    photo_url = product.photo_url  # запоминаем до удаления

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

    # Записываем событие удаления продукта для биллинга/аудита.
    # Ошибка записи не пробрасывается — продукт уже удалён.
    try:
        db.add(UsageEvent(restaurant_id=restaurant.id, event_type="product_deleted"))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Не удалось записать UsageEvent product_deleted: product_id=%s restaurant_id=%s",
            product_id, restaurant.id,
        )

    # Удаляем фото из R2 после успешного commit
    if photo_url:
        _delete_r2_photo(photo_url)
