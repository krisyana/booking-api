"""Booking rules.

Concurrency model: every operation that changes how many seats a slot has taken first takes a
row lock on that slot (SELECT ... FOR UPDATE). Two requests for the same slot therefore run one
after the other, so the capacity check can never be raced. Locks are always taken in the same
order (slot first, then booking) to rule out deadlocks.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Booking, BookingStatus, Slot, utcnow
from app.services.outbox import enqueue


class SlotNotFound(Exception):
    pass


class SlotClosed(Exception):
    """The slot has already started."""


class SlotFull(Exception):
    def __init__(self, remaining: int):
        self.remaining = remaining
        super().__init__(f"only {remaining} seats left")


class BookingNotFound(Exception):
    pass


class IdempotencyKeyReused(Exception):
    """The same Idempotency-Key was sent with a different request body."""


def counts_toward_capacity(now: datetime):
    """A booking takes seats if it is paid, or unpaid but its hold has not lapsed."""
    return or_(
        Booking.status == BookingStatus.CONFIRMED,
        and_(Booking.status == BookingStatus.PENDING, Booking.hold_expires_at > now),
    )


def hold_owns_seats(booking: Booking, now: datetime) -> bool:
    """The unpaid half of `counts_toward_capacity`, for a booking already loaded."""
    return booking.status == BookingStatus.PENDING and booking.hold_expires_at > now


async def lock_slot(session: AsyncSession, slot_id: uuid.UUID) -> Slot | None:
    return await session.scalar(select(Slot).where(Slot.id == slot_id).with_for_update())


async def seats_taken(session: AsyncSession, slot_id: uuid.UUID, now: datetime) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Booking.party_size), 0)).where(
            Booking.slot_id == slot_id, counts_toward_capacity(now)
        )
    )
    return int(total or 0)


async def create_booking(
    session: AsyncSession,
    *,
    slot_id: uuid.UUID,
    customer_name: str,
    customer_whatsapp: str,
    party_size: int,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> tuple[Booking, bool]:
    """Hold seats for an unpaid booking. Call inside a transaction.

    Returns (booking, created). With an `idempotency_key`, repeating the same request returns the
    original booking (created=False) instead of holding seats twice, so a customer who double-taps
    or retries after a timeout is not charged for two bookings.
    """
    now = now or utcnow()
    slot = await lock_slot(session, slot_id)
    if slot is None:
        raise SlotNotFound

    if idempotency_key is not None:
        # The slot lock serialises concurrent retries, so this check cannot be raced.
        existing = await session.scalar(
            select(Booking).where(
                Booking.slot_id == slot.id, Booking.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            same = (existing.customer_name, existing.customer_whatsapp, existing.party_size) == (
                customer_name,
                customer_whatsapp,
                party_size,
            )
            if not same:
                raise IdempotencyKeyReused
            return existing, False

    if slot.starts_at <= now:
        raise SlotClosed

    remaining = slot.capacity - await seats_taken(session, slot.id, now)
    if party_size > remaining:
        raise SlotFull(max(remaining, 0))

    booking = Booking(
        slot_id=slot.id,
        customer_name=customer_name,
        customer_whatsapp=customer_whatsapp,
        party_size=party_size,
        total_idr=slot.price_idr * party_size,
        status=BookingStatus.PENDING,
        hold_expires_at=now + timedelta(minutes=settings.hold_minutes),
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(booking)
    await session.flush()
    enqueue(session, "booking_held", booking)
    return booking, True


async def cancel_booking(session: AsyncSession, booking_id: uuid.UUID) -> Booking:
    """Cancel a booking and free its seats. Anything already paid becomes refund due."""
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFound
    # Slot first, then booking, same order as everywhere else.
    await lock_slot(session, booking.slot_id)
    booking = await session.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert booking is not None

    if booking.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
        booking.status = BookingStatus.CANCELLED
        # A paid booking keeps its paid_idr, so refund_due_idr now equals everything received.
        enqueue(session, "booking_cancelled", booking)
    return booking


async def expire_holds(session: AsyncSession, now: datetime | None = None) -> int:
    """Mark unpaid bookings whose hold has lapsed as expired. Returns how many.

    Capacity checks already ignore lapsed holds, so this keeps statuses honest. It is safe to run
    at any time and from several processes at once. A booking that was partly paid when its hold
    ran out is owed money back, so its customer is told.
    """
    now = now or utcnow()
    expired = (
        await session.scalars(
            update(Booking)
            .where(Booking.status == BookingStatus.PENDING, Booking.hold_expires_at <= now)
            .values(status=BookingStatus.EXPIRED)
            .returning(Booking)
            .execution_options(synchronize_session=False)
        )
    ).all()
    for booking in expired:
        if booking.paid_idr > 0:
            enqueue(session, "refund_due", booking)
    return len(expired)
