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


def counts_toward_capacity(now: datetime):
    """A booking takes seats if it is paid, or unpaid but its hold has not lapsed."""
    return or_(
        Booking.status == BookingStatus.CONFIRMED,
        and_(Booking.status == BookingStatus.PENDING, Booking.hold_expires_at > now),
    )


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
    now: datetime | None = None,
) -> Booking:
    """Hold seats for an unpaid booking. Call inside a transaction."""
    now = now or utcnow()
    slot = await lock_slot(session, slot_id)
    if slot is None:
        raise SlotNotFound
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
        created_at=now,
    )
    session.add(booking)
    await session.flush()
    return booking


async def cancel_booking(
    session: AsyncSession, booking_id: uuid.UUID, now: datetime | None = None
) -> Booking:
    """Cancel a booking and free its seats. A paid booking is flagged for refund."""
    now = now or utcnow()
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

    if booking.status == BookingStatus.CONFIRMED:
        booking.refund_due = True
    if booking.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
        booking.status = BookingStatus.CANCELLED
    return booking


async def expire_holds(session: AsyncSession, now: datetime | None = None) -> int:
    """Mark unpaid bookings whose hold has lapsed as expired. Returns how many.

    Capacity checks already ignore lapsed holds, so this is housekeeping that keeps statuses
    honest; it is safe to run at any time and from several processes at once.
    """
    now = now or utcnow()
    result = await session.execute(
        update(Booking)
        .where(Booking.status == BookingStatus.PENDING, Booking.hold_expires_at <= now)
        .values(status=BookingStatus.EXPIRED)
    )
    return result.rowcount or 0
