import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.deps import OperatorDep, SessionDep
from app.models import Activity, Booking, Slot
from app.schemas import BookingIn, BookingOut
from app.services import bookings as service

router = APIRouter(tags=["bookings"])


@router.post(
    "/slots/{slot_id}/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED
)
async def create_booking(slot_id: uuid.UUID, body: BookingIn, session: SessionDep):
    """Hold seats for HOLD_MINUTES. The booking is confirmed when the payment webhook arrives."""
    try:
        async with session.begin():
            booking = await service.create_booking(session, slot_id=slot_id, **body.model_dump())
    except service.SlotNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slot not found") from None
    except service.SlotClosed:
        raise HTTPException(status.HTTP_409_CONFLICT, "this slot has already started") from None
    except service.SlotFull as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"not enough seats: {e.remaining} left"
        ) from None
    return booking


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(booking_id: uuid.UUID, session: SessionDep):
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
    return booking


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(booking_id: uuid.UUID, session: SessionDep):
    try:
        async with session.begin():
            booking = await service.cancel_booking(session, booking_id)
    except service.BookingNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found") from None
    return booking


@router.get("/slots/{slot_id}/bookings", response_model=list[BookingOut])
async def slot_roster(slot_id: uuid.UUID, operator: OperatorDep, session: SessionDep):
    """Operator-only: everyone booked on one of your slots."""
    owned = await session.scalar(
        select(Slot.id)
        .join(Activity, Activity.id == Slot.activity_id)
        .where(Slot.id == slot_id, Activity.operator_id == operator.id)
    )
    if owned is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slot not found")
    result = await session.scalars(
        select(Booking).where(Booking.slot_id == slot_id).order_by(Booking.created_at)
    )
    return list(result)
