"""Holds that are never paid must give their seats back; late payments must be handled honestly."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Booking, BookingStatus, OutboxMessage, utcnow
from app.services.bookings import SlotFull, create_booking, expire_holds
from app.services.payments import Outcome, apply_payment_event
from tests.conftest import seed_slot


async def _hold(slot_id, now, party_size=1):
    async with SessionLocal() as session, session.begin():
        booking, _ = await create_booking(
            session,
            slot_id=slot_id,
            customer_name="Ayu",
            customer_whatsapp="+6281200000000",
            party_size=party_size,
            now=now,
        )
    return booking


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
        assert stored.refund_due_idr == 100_000


async def _outbox_kinds() -> list[str]:
    async with SessionLocal() as session:
        messages = await session.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at))
        return [m.kind for m in messages]


async def test_expiring_a_partly_paid_booking_tells_the_customer_they_are_owed_money():
    slot_id = await seed_slot(capacity=2, price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    async with SessionLocal() as session, session.begin():
        await apply_payment_event(
            session,
            event_id="evt_half",
            booking_id=booking.id,
            status="paid",
            amount_idr=40_000,
            now=start,
        )

    async with SessionLocal() as session, session.begin():
        await expire_holds(session, now=start + timedelta(minutes=settings.hold_minutes + 1))

    assert await _outbox_kinds() == ["booking_held", "refund_due"]
    async with SessionLocal() as session:
        assert (await session.get(Booking, booking.id)).refund_due_idr == 40_000


async def test_expiring_an_unpaid_booking_sends_no_refund_message():
    slot_id = await seed_slot()
    start = utcnow()
    await _hold(slot_id, start)

    async with SessionLocal() as session, session.begin():
        await expire_holds(session, now=start + timedelta(minutes=settings.hold_minutes + 1))

    assert await _outbox_kinds() == ["booking_held"]


async def test_a_partial_payment_on_an_expired_booking_is_refund_due():
    slot_id = await seed_slot(price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    async with SessionLocal() as session, session.begin():
        await expire_holds(session, now=start + timedelta(minutes=settings.hold_minutes + 1))

    async with SessionLocal() as session, session.begin():
        outcome = await apply_payment_event(
            session,
            event_id="evt_after_expiry",
            booking_id=booking.id,
            status="paid",
            amount_idr=30_000,
            now=start + timedelta(minutes=settings.hold_minutes + 2),
        )

    assert outcome == Outcome.REFUND_DUE
    assert await _outbox_kinds() == ["booking_held", "refund_due"]


async def _pay(booking_id, event_id, amount_idr, now) -> Outcome:
    async with SessionLocal() as session, session.begin():
        return await apply_payment_event(
            session,
            event_id=event_id,
            booking_id=booking_id,
            status="paid",
            amount_idr=amount_idr,
            now=now,
        )


async def test_a_top_up_after_a_refund_was_announced_stays_refund_due():
    slot_id = await seed_slot(capacity=1, price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    after_lapse = start + timedelta(minutes=settings.hold_minutes + 1)
    async with SessionLocal() as session, session.begin():
        await expire_holds(session, now=after_lapse)

    short = await _pay(booking.id, "evt_short", 30_000, after_lapse)
    top_up = await _pay(booking.id, "evt_top_up", 70_000, after_lapse + timedelta(minutes=1))

    assert (short, top_up) == (Outcome.REFUND_DUE, Outcome.REFUND_DUE)
    async with SessionLocal() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.status == BookingStatus.EXPIRED
        assert (stored.paid_idr, stored.refund_due_idr) == (100_000, 100_000)
    assert await _outbox_kinds() == ["booking_held", "refund_due", "refund_due"]


async def test_a_short_payment_on_a_lapsed_hold_is_refund_due_before_expiry_runs():
    slot_id = await seed_slot(capacity=1, price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    after_lapse = start + timedelta(minutes=settings.hold_minutes + 1)

    outcome = await _pay(booking.id, "evt_short_lapsed", 40_000, after_lapse)

    assert outcome == Outcome.REFUND_DUE
    async with SessionLocal() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.status == BookingStatus.EXPIRED
        assert stored.refund_due_idr == 40_000
    assert await _outbox_kinds() == ["booking_held", "refund_due"]
    # The seat was released, matching the outcome: someone else can take it.
    other = await _hold(slot_id, after_lapse)
    assert other.status == BookingStatus.PENDING
    async with SessionLocal() as session, session.begin():
        assert await expire_holds(session, now=after_lapse) == 0


async def test_a_single_covering_payment_after_expiry_is_late_confirmed():
    slot_id = await seed_slot(capacity=1, price_idr=100_000)
    start = utcnow()
    booking = await _hold(slot_id, start)
    after_lapse = start + timedelta(minutes=settings.hold_minutes + 1)
    async with SessionLocal() as session, session.begin():
        await expire_holds(session, now=after_lapse)

    outcome = await _pay(booking.id, "evt_full_late", 100_000, after_lapse + timedelta(minutes=1))

    assert outcome == Outcome.LATE_CONFIRMED
    async with SessionLocal() as session:
        stored = await session.get(Booking, booking.id)
        assert stored.status == BookingStatus.CONFIRMED
        assert stored.refund_due_idr == 0
    assert await _outbox_kinds() == ["booking_held", "booking_confirmed"]
