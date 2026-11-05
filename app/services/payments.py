"""Payment webhook handling.

Payment providers retry webhooks and sometimes deliver them twice or out of order, so every
event is recorded under its provider event id (primary key) before anything else happens. A
repeated event hits the conflict and changes nothing.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, BookingStatus, PaymentEvent, utcnow
from app.services.bookings import lock_slot, seats_taken


class Outcome(StrEnum):
    DUPLICATE = "duplicate"  # event id already processed, nothing changed
    CONFIRMED = "confirmed"
    LATE_CONFIRMED = "late_confirmed"  # hold had lapsed but seats were still free
    REFUND_DUE = "refund_due"  # paid, but the seats are gone or the booking was cancelled
    AMOUNT_MISMATCH = "amount_mismatch"  # paid amount differs from the booking total
    FAILED = "failed"  # provider reported a failed payment
    UNKNOWN_BOOKING = "unknown_booking"


async def apply_payment_event(
    session: AsyncSession,
    *,
    event_id: str,
    booking_id: uuid.UUID,
    status: str,
    amount_idr: int,
    now: datetime | None = None,
) -> Outcome:
    """Process one provider event. Call inside a transaction."""
    now = now or utcnow()

    inserted = await session.execute(
        pg_insert(PaymentEvent)
        .values(
            event_id=event_id,
            booking_id=booking_id,
            status=status,
            amount_idr=amount_idr,
            received_at=now,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(PaymentEvent.event_id)
    )
    if inserted.scalar_one_or_none() is None:
        return Outcome.DUPLICATE

    outcome = await _decide(session, booking_id, status, amount_idr, now)
    await session.execute(
        PaymentEvent.__table__.update()
        .where(PaymentEvent.event_id == event_id)
        .values(outcome=outcome.value)
    )
    return outcome


async def _decide(
    session: AsyncSession, booking_id: uuid.UUID, status: str, amount_idr: int, now: datetime
) -> Outcome:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return Outcome.UNKNOWN_BOOKING

    # Lock order: slot, then booking.
    slot = await lock_slot(session, booking.slot_id)
    booking = await session.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert booking is not None and slot is not None

    if status != "paid":
        if booking.status == BookingStatus.PENDING:
            booking.status = BookingStatus.CANCELLED
        return Outcome.FAILED

    if amount_idr != booking.total_idr:
        # Do not confirm on the wrong amount; a person needs to look at this one.
        booking.refund_due = True
        return Outcome.AMOUNT_MISMATCH

    if booking.status == BookingStatus.CONFIRMED:
        return Outcome.CONFIRMED  # paid twice under different event ids: already ours

    if booking.status == BookingStatus.CANCELLED:
        booking.refund_due = True
        return Outcome.REFUND_DUE

    hold_lapsed = booking.status == BookingStatus.EXPIRED or booking.hold_expires_at <= now
    if hold_lapsed:
        # Our hold no longer counts toward capacity, so check the seats are still free.
        free = slot.capacity - await seats_taken(session, slot.id, now)
        if booking.party_size > free or slot.starts_at <= now:
            booking.status = BookingStatus.EXPIRED
            booking.refund_due = True
            return Outcome.REFUND_DUE
        booking.status = BookingStatus.CONFIRMED
        return Outcome.LATE_CONFIRMED

    booking.status = BookingStatus.CONFIRMED
    return Outcome.CONFIRMED
