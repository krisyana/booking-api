import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select

from app.config import settings
from app.deps import CustomerBookingDep, OperatorDep, SessionDep
from app.integrations.payments import PaymentProvider, get_payment_provider
from app.models import Activity, Booking, BookingStatus, Slot, utcnow
from app.schemas import BookingCreated, BookingIn, BookingOut, ChargeOut
from app.security import booking_token
from app.services import bookings as service

router = APIRouter(tags=["bookings"])


def provider_dependency() -> PaymentProvider:
    try:
        return get_payment_provider()
    except NotImplementedError as e:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e)) from None


@router.post(
    "/slots/{slot_id}/bookings", response_model=BookingCreated, status_code=status.HTTP_201_CREATED
)
async def create_booking(
    slot_id: uuid.UUID,
    body: BookingIn,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=120)] = None,
):
    """Hold seats for HOLD_MINUTES. The booking is confirmed when the payment webhook arrives.

    Send an `Idempotency-Key` header to make retries safe: repeating the request returns the same
    booking (status 200) instead of holding seats a second time.
    """
    try:
        async with session.begin():
            booking, created = await service.create_booking(
                session, slot_id=slot_id, idempotency_key=idempotency_key, **body.model_dump()
            )
    except service.SlotNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slot not found") from None
    except service.SlotClosed:
        raise HTTPException(status.HTTP_409_CONFLICT, "this slot has already started") from None
    except service.SlotFull as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"not enough seats: {e.remaining} left"
        ) from None
    except service.IdempotencyKeyReused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "this Idempotency-Key was already used with a different request",
        ) from None

    if not created:
        response.status_code = status.HTTP_200_OK
    return BookingCreated(
        **BookingOut.model_validate(booking).model_dump(),
        access_token=booking_token(booking.id, settings.token_secret),
    )


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(booking: CustomerBookingDep):
    """Needs the booking's `X-Booking-Token`."""
    return booking


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(booking: CustomerBookingDep, session: SessionDep):
    """Needs the booking's `X-Booking-Token`. Anything already paid becomes refund due."""
    cancelled = await service.cancel_booking(session, booking.id)
    await session.commit()  # the token check already opened a transaction on this session
    return cancelled


@router.post("/bookings/{booking_id}/pay", response_model=ChargeOut)
async def start_payment(
    booking: CustomerBookingDep,
    session: SessionDep,
    provider: Annotated[PaymentProvider, Depends(provider_dependency)],
):
    """Create (or return the existing) charge for what is still unpaid on this booking.

    PLACEHOLDER: with PAYMENT_PROVIDER=fake no money moves; see docs/mvp.md.
    """
    # Lock the booking so two taps on "pay" cannot create two charges.
    await session.refresh(booking, with_for_update=True)
    if booking.status != BookingStatus.PENDING or booking.hold_expires_at <= utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "booking is not awaiting payment")

    if booking.payment_reference is None:
        charge = await provider.create_charge(
            booking_id=booking.id,
            amount_idr=booking.total_idr - booking.paid_idr,
            expires_at=booking.hold_expires_at,
            description=f"Booking {booking.id}",
        )
        booking.payment_reference = charge.reference
        booking.payment_url = charge.payment_url
    await session.commit()

    assert booking.payment_reference and booking.payment_url
    return ChargeOut(
        reference=booking.payment_reference,
        payment_url=booking.payment_url,
        amount_idr=booking.total_idr - booking.paid_idr,
        expires_at=booking.hold_expires_at,
    )


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
