from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models import WaiterCall, RestaurantTable, Restaurant
from schemas import WaiterCallCreate, WaiterCallResponse, WaiterCallStatusUpdate

router = APIRouter()

VALID_STATUSES = ["active", "accepted", "completed", "cancelled"]


@router.post("/", response_model=WaiterCallResponse)
def create_waiter_call(data: WaiterCallCreate, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == data.restaurant_id,
        Restaurant.is_active == True
    ).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    table = db.query(RestaurantTable).filter(
        RestaurantTable.id == data.table_id,
        RestaurantTable.restaurant_id == data.restaurant_id
    ).first()

    if not table:
        raise HTTPException(status_code=404, detail="Стол не найден")

    existing_call = db.query(WaiterCall).filter(
        WaiterCall.restaurant_id == data.restaurant_id,
        WaiterCall.table_id == data.table_id,
        WaiterCall.status.in_(["active", "accepted"])
    ).first()

    if existing_call:
        raise HTTPException(status_code=400, detail="Для этого стола уже есть активный вызов")

    call = WaiterCall(
        restaurant_id=data.restaurant_id,
        table_id=data.table_id,
        status="active"
    )

    db.add(call)
    db.commit()
    db.refresh(call)
    return call


@router.get("/restaurant/{restaurant_id}", response_model=List[WaiterCallResponse])
def get_waiter_calls(
    restaurant_id: int,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(WaiterCall).filter(
        WaiterCall.restaurant_id == restaurant_id
    )

    if status_filter:
        query = query.filter(WaiterCall.status == status_filter)

    return query.order_by(WaiterCall.created_at.desc()).all()


@router.patch("/{call_id}/status", response_model=WaiterCallResponse)
def update_status(call_id: int, data: WaiterCallStatusUpdate, db: Session = Depends(get_db)):
    if data.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый статус. Допустимые: {VALID_STATUSES}"
        )

    call = db.query(WaiterCall).filter(WaiterCall.id == call_id).first()

    if not call:
        raise HTTPException(status_code=404, detail="Вызов не найден")

    call.status = data.status
    db.commit()
    db.refresh(call)
    return call
