"""Holds that are never paid must give their seats back; late payments must be handled honestly."""

from datetime import timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import BookingStatus, utcnow
from app.services.bookings import SlotFull, create_booking, expire_holds
from app.services.payments import Outcome, apply_payment_event
from tests.conftest import seed_slot


async def _hold(slot_id, now, party_size=1):
    async with SessionLocal() as session, session.begin():
        return await create_booking(
            session,
            slot_id=slot_id,
            customer_name="Ayu",
            customer_whatsapp="+6281200000000",
            party_size=party_size,
            now=now,
        )


async def test_a_lapsed_hold_frees_its_seat_for_someone_else():
    slot_id = await seed_slot(capacity=1)
    start = utcnow()
    await _hold(slot_id, start)

    with pytest.raises(SlotFull):
        await _hold(slot_id, start + timedelta(minutes=settings.hold_minutes - 1))

    second = await _hold(slot_id, start + timedelta(minutes=settings.hold_minutes + 1))
    assert second.status == BookingStatus.PENDING


async def test_expire_holds_marks_only_lapsed_unpaid_bookings():
    slot_id = await seed_slot(capacity=5)
    start = utcnow()
    old = await _hold(slot_id, start)
    fresh = await _hold(slot_id, start + timedelta(minutes=settings.hold_minutes))

    async with SessionLocal() as session, session.begin():
        expired = await expire_holds(
            session, now=start + timedelta(minutes=settings.hold_minutes + 1)
        )
    assert expired == 1

    async with SessionLocal() as session:
        from app.models import Booking

        assert (await session.get(Booking, old.id)).status == BookingStatus.EXPIRED
        assert (await session.get(Booking, fresh.id)).status == BookingStatus.PENDING


async def test_late_payment_is_accepted_if_the_seat_is_still_free():
    slot_id = await seed_slot(capacity=1, price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    late = start + timedelta(minutes=settings.hold_minutes + 5)

    async with SessionLocal() as session, session.begin():
        outcome = await apply_payment_event(
            session,
            event_id="evt_late_ok",
            booking_id=booking.id,
            status="paid",
            amount_idr=100_000,
            now=late,
        )

    assert outcome == Outcome.LATE_CONFIRMED


async def test_late_payment_needs_a_refund_if_the_seat_was_taken():
    slot_id = await seed_slot(capacity=1, price_idr=100_000)
    start = utcnow()
    first = await _hold(slot_id, start)
    after_lapse = start + timedelta(minutes=settings.hold_minutes + 1)
    await _hold(slot_id, after_lapse)  # someone else grabs the freed seat

    async with SessionLocal() as session, session.begin():
        outcome = await apply_payment_event(
            session,
            event_id="evt_late_taken",
            booking_id=first.id,
            status="paid",
            amount_idr=100_000,
            now=after_lapse + timedelta(minutes=1),
        )

    assert outcome == Outcome.REFUND_DUE
    async with SessionLocal() as session:
        from app.models import Booking

        stored = await session.get(Booking, first.id)
        assert stored.status == BookingStatus.EXPIRED
        assert stored.refund_due is True
