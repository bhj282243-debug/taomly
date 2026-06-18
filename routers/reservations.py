from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import Reservation, Restaurant
from schemas import ReservationCreate, ReservationResponse, ReservationStatusUpdate

router = APIRouter()


@router.post("/", response_model=ReservationResponse)
def create_reservation(data: ReservationCreate, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(
        Restaurant.id == data.restaurant_id,
        Restaurant.is_active == True
    ).first()

    if not restaurant:
        raise HTTPException(status_code=404, detail="Ресторан не найден")

    reservation = Reservation(
        restaurant_id=data.restaurant_id,
        client_name=data.client_name,
        client_phone=data.client_phone,
        guests_count=data.guests_count,
        reservation_time=data.reservation_time,
        comment=data.comment,
        status="new"
    )

    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    return reservation


@router.get("/restaurant/{restaurant_id}", response_model=List[ReservationResponse])
def get_reservations(restaurant_id: int, db: Session = Depends(get_db)):
    return db.query(Reservation).filter(
        Reservation.restaurant_id == restaurant_id
    ).order_by(Reservation.reservation_time).all()


@router.patch("/{reservation_id}/status", response_model=ReservationResponse)
def update_status(reservation_id: int, data: ReservationStatusUpdate, db: Session = Depends(get_db)):
    reservation = db.query(Reservation).filter(
        Reservation.id == reservation_id
    ).first()

    if not reservation:
        raise HTTPException(status_code=404, detail="Бронь не найдена")

    reservation.status = data.status
    db.commit()
    db.refresh(reservation)
    return reservation
