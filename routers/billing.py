"""
routers/billing.py — Taomly Billing System
Простая SaaS-модель подписок. Без интеграции платёжных систем.

Endpoints:
  GET  /api/billing/plans                — список тарифных планов из БД
  GET  /api/billing/subscription         — текущая подписка ресторана
  POST /api/billing/subscribe/{plan_id}  — сменить/активировать план
  GET  /api/billing/usage                — использование за текущий месяц
  GET  /api/billing/invoice/{month}      — PDF-подтверждение подписки

Принцип: демонстрирует готовую SaaS-модель, повышает стоимость Taomly.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth import get_current_restaurant_admin
from database import get_db
from models import Restaurant, Subscription, SubscriptionPlan, UsageEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ──────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────

class PlanResponse(BaseModel):
    id: int
    name: str
    price: int
    currency: str
    orders_per_month: int   # -1 = безлимит
    products_limit: int     # -1 = безлимит
    users_limit: int        # -1 = безлимит
    description: Optional[str]


class SubscriptionResponse(BaseModel):
    plan_id: int
    plan_name: str
    price: int
    currency: str
    orders_per_month: int
    products_limit: int
    started_at: str
    expires_at: Optional[str]   # None = бессрочно
    is_active: bool


class UsageResponse(BaseModel):
    period: str           # "2025-07"
    orders_used: int
    orders_limit: int     # -1 = безлимит
    orders_remaining: int # -1 = безлимит
    orders_pct: int       # 0-100, -1 если безлимит
    products_used: int
    products_limit: int
    products_remaining: int
    products_pct: int


class SubscribeResponse(BaseModel):
    success: bool
    plan_id: int
    plan_name: str
    message: str


# ──────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────

def _get_active_subscription(db: Session, restaurant_id: int) -> Optional[Subscription]:
    """Возвращает активную подписку или None."""
    return (
        db.query(Subscription)
        .filter(
            Subscription.restaurant_id == restaurant_id,
            Subscription.is_active == True,
        )
        .order_by(Subscription.started_at.desc())
        .first()
    )


def _get_free_plan(db: Session) -> SubscriptionPlan:
    """Возвращает Free план из БД (всегда должен существовать)."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == "Free").first()
    if not plan:
        # Fallback если миграция не выполнена
        raise HTTPException(
            status_code=503,
            detail="Тарифные планы не найдены. Выполните MIGRATION_billing.sql в Neon.",
        )
    return plan


def _count_orders_this_month(db: Session, restaurant_id: int) -> int:
    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = db.execute(
        text("""
            SELECT COUNT(*) AS cnt FROM orders
            WHERE restaurant_id = :rid
              AND created_at   >= :start
              AND status       != 'cancelled'
        """),
        {"rid": restaurant_id, "start": month_start},
    ).fetchone()
    return int(row.cnt) if row else 0


def _count_products(db: Session, restaurant_id: int) -> int:
    row = db.execute(
        text("SELECT COUNT(*) AS cnt FROM products WHERE restaurant_id = :rid"),
        {"rid": restaurant_id},
    ).fetchone()
    return int(row.cnt) if row else 0


def _pct(used: int, limit: int) -> int:
    """Процент использования. -1 если безлимит."""
    if limit == -1:
        return -1
    return min(100, round((used / max(limit, 1)) * 100))


def _remaining(used: int, limit: int) -> int:
    """Остаток. -1 если безлимит."""
    if limit == -1:
        return -1
    return max(0, limit - used)


# ──────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────

@router.get("/plans", response_model=List[PlanResponse])
def get_plans(db: Session = Depends(get_db)) -> List[PlanResponse]:
    """Список активных тарифных планов из БД."""
    plans = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active == True)
        .order_by(SubscriptionPlan.id)
        .all()
    )
    if not plans:
        raise HTTPException(status_code=503, detail="Выполните MIGRATION_billing.sql в Neon.")
    return [PlanResponse(
        id=p.id,
        name=p.name,
        price=p.price,
        currency=p.currency,
        orders_per_month=p.orders_per_month,
        products_limit=p.products_limit,
        users_limit=p.users_limit,
        description=p.description,
    ) for p in plans]


@router.get("/subscription", response_model=SubscriptionResponse)
def get_subscription(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    """Текущая подписка ресторана. Если нет — возвращает Free."""
    sub = _get_active_subscription(db, restaurant.id)

    if sub:
        plan = sub.plan
        started_at = sub.started_at.isoformat()
        expires_at = sub.expires_at.isoformat() if sub.expires_at else None
    else:
        plan = _get_free_plan(db)
        started_at = datetime.now(tz=timezone.utc).isoformat()
        expires_at = None

    return SubscriptionResponse(
        plan_id=plan.id,
        plan_name=plan.name,
        price=plan.price,
        currency=plan.currency,
        orders_per_month=plan.orders_per_month,
        products_limit=plan.products_limit,
        started_at=started_at,
        expires_at=expires_at,
        is_active=True,
    )


@router.post("/subscribe/{plan_id}", response_model=SubscribeResponse)
def subscribe(
    plan_id: int = Path(..., ge=1),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> SubscribeResponse:
    """
    Сменить тарифный план.
    Деактивирует текущий, создаёт новый.
    Без реальных платежей — архитектура готова под Stripe/Payme/Click (Stage 3+).
    """
    plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.id == plan_id,
        SubscriptionPlan.is_active == True,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Тарифный план не найден")

    now = datetime.now(tz=timezone.utc)

    # Деактивировать текущие подписки
    db.query(Subscription).filter(
        Subscription.restaurant_id == restaurant.id,
        Subscription.is_active == True,
    ).update({"is_active": False})

    # Создать новую
    new_sub = Subscription(
        restaurant_id=restaurant.id,
        plan_id=plan.id,
        started_at=now,
        expires_at=None,   # бессрочно до интеграции оплаты
        is_active=True,
    )
    db.add(new_sub)
    db.commit()

    logger.info("Restaurant %d subscribed to plan '%s'", restaurant.id, plan.name)

    return SubscribeResponse(
        success=True,
        plan_id=plan.id,
        plan_name=plan.name,
        message=f"'{plan.name}' tarifi muvaffaqiyatli ulandi",
    )


@router.get("/usage", response_model=UsageResponse)
def get_usage(
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
) -> UsageResponse:
    """Использование ресурсов за текущий месяц с остатком."""
    sub = _get_active_subscription(db, restaurant.id)
    plan = sub.plan if sub else _get_free_plan(db)

    orders_used   = _count_orders_this_month(db, restaurant.id)
    products_used = _count_products(db, restaurant.id)

    now = datetime.now(tz=timezone.utc)

    return UsageResponse(
        period=f"{now.year}-{now.month:02d}",
        orders_used=orders_used,
        orders_limit=plan.orders_per_month,
        orders_remaining=_remaining(orders_used, plan.orders_per_month),
        orders_pct=_pct(orders_used, plan.orders_per_month),
        products_used=products_used,
        products_limit=plan.products_limit,
        products_remaining=_remaining(products_used, plan.products_limit),
        products_pct=_pct(products_used, plan.products_limit),
    )


@router.get("/invoice/{month}")
def get_invoice(
    month: int = Path(..., ge=1, le=12),
    restaurant: Restaurant = Depends(get_current_restaurant_admin),
    db: Session = Depends(get_db),
):
    """
    PDF-подтверждение подписки за указанный месяц текущего года.
    Не является платёжным документом — только подтверждение тарифа.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle,
            Paragraph, Spacer, HRFlowable,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="reportlab не установлен. Добавьте 'reportlab' в requirements.txt",
        )

    sub  = _get_active_subscription(db, restaurant.id)
    plan = sub.plan if sub else _get_free_plan(db)

    now   = datetime.now(tz=timezone.utc)
    year  = now.year

    # Заказы за месяц
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    month_end   = datetime(year if month < 12 else year + 1, month % 12 + 1, 1, tzinfo=timezone.utc)

    row = db.execute(
        text("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS revenue
            FROM orders
            WHERE restaurant_id = :rid
              AND created_at   >= :start
              AND created_at   <  :end
              AND status       != 'cancelled'
        """),
        {"rid": restaurant.id, "start": month_start, "end": month_end},
    ).fetchone()
    orders_count = int(row.cnt)
    revenue      = int(row.revenue)

    month_names = ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
                   "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]

    # ── PDF ──────────────────────────────────────────────────
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2.5*cm, leftMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
    )
    styles = getSampleStyleSheet()
    C_DARK = colors.HexColor("#111111")
    C_GOLD = colors.HexColor("#D4A853")
    C_GRAY = colors.HexColor("#666666")
    C_LINE = colors.HexColor("#DDDDDD")

    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold",   fontSize=22, textColor=C_DARK,  spaceAfter=4,  alignment=TA_CENTER)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold",   fontSize=13, textColor=C_DARK,  spaceAfter=10)
    sm = ParagraphStyle("sm", fontName="Helvetica",        fontSize=10, textColor=C_GRAY,  spaceAfter=4)
    bd = ParagraphStyle("bd", fontName="Helvetica-Bold",   fontSize=11, textColor=C_DARK,  spaceAfter=4)
    ft = ParagraphStyle("ft", fontName="Helvetica",        fontSize=8,  textColor=C_GRAY,  alignment=TA_CENTER)

    price_label = f"{plan.currency} {plan.price}" if plan.price > 0 else "Bepul"
    invoice_num = f"TML-{year}{month:02d}-{restaurant.id:04d}"

    story = [
        Paragraph("TAOMLY", h1),
        Paragraph("Restaurant Commerce Platform", ParagraphStyle(
            "sub", fontName="Helvetica", fontSize=10, textColor=C_GOLD,
            spaceAfter=16, alignment=TA_CENTER,
        )),
        HRFlowable(width="100%", thickness=1, color=C_LINE, spaceAfter=16),

        Paragraph(f"Obuna tasdiqi  •  {invoice_num}", h2),

        Paragraph(f"<b>Restoran:</b> {restaurant.name}", sm),
        Paragraph(f"<b>Davr:</b> {month_names[month]} {year}", sm),
        Paragraph(f"<b>Tarif:</b> {plan.name}  —  {price_label}/oy", sm),
        Paragraph(
            f"<b>Amal qilish muddati:</b> {'Muddatsiz' if not (sub and sub.expires_at) else sub.expires_at.strftime('%d.%m.%Y')}",
            sm,
        ),

        Spacer(1, 0.6*cm),
        HRFlowable(width="100%", thickness=0.5, color=C_LINE, spaceAfter=12),

        Paragraph("Foydalanish ma'lumotlari", h2),
    ]

    orders_limit_label   = "Cheksiz" if plan.orders_per_month == -1 else str(plan.orders_per_month)
    products_limit_label = "Cheksiz" if plan.products_limit   == -1 else str(plan.products_limit)

    tbl_data = [
        ["Ko'rsatkich",          "Ishlatilgan",     "Limit"],
        ["Buyurtmalar (bu oy)",  str(orders_count), orders_limit_label],
        ["Mahsulotlar",          str(_count_products(db, restaurant.id)), products_limit_label],
        ["Restoran tushumi",     f"{revenue:,} so'm", "—"],
    ]
    tbl = Table(tbl_data, colWidths=[9*cm, 4*cm, 4*cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#F9F9F9"), colors.white]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_LINE),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
    ]))
    story.append(tbl)

    story += [
        Spacer(1, 1.2*cm),
        HRFlowable(width="100%", thickness=0.5, color=C_LINE, spaceAfter=10),
        Paragraph(f"Sana: {now.strftime('%d.%m.%Y')}", ft),
        Paragraph("Taomly Platform  |  taomly.uz  |  admin@taomly.uz", ft),
        Paragraph("Ushbu hujjat to'lov cheki emas — obuna tasdiqidir.", ft),
    ]

    doc.build(story)
    buf.seek(0)

    filename = f"taomly-subscription-{year}{month:02d}-{restaurant.slug}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
