"""The reason this project exists: many people booking the same slot at the same moment."""

import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Booking, BookingStatus
from tests.conftest import seed_slot


async def test_concurrent_bookings_never_exceed_capacity(client, booking_payload):
    slot_id = await seed_slot(capacity=5)

    responses = await asyncio.gather(
        *(client.post(f"/slots/{slot_id}/bookings", json=booking_payload) for _ in range(20))
    )

    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 5
    assert codes.count(409) == 15
    async with SessionLocal() as session:
        held = await session.scalar(
            select(func.sum(Booking.party_size)).where(Booking.status == BookingStatus.PENDING)
        )
    assert held == 5


async def test_concurrent_groups_of_different_sizes_stay_within_capacity(client, booking_payload):
    slot_id = await seed_slot(capacity=10)
    sizes = [3, 2, 4, 1, 5, 2, 3, 1, 2, 4, 3, 2]

    responses = await asyncio.gather(
        *(
            client.post(f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": n})
            for n in sizes
        )
    )

    accepted = sum(n for n, r in zip(sizes, responses, strict=True) if r.status_code == 201)
    assert accepted <= 10
    async with SessionLocal() as session:
        held = await session.scalar(select(func.sum(Booking.party_size)))
    assert held == accepted


async def test_cancel_and_book_racing_each_other_stays_consistent(client, booking_payload):
    slot_id = await seed_slot(capacity=1)
    first = (await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)).json()

    results = await asyncio.gather(
        client.post(f"/bookings/{first['id']}/cancel"),
        *(client.post(f"/slots/{slot_id}/bookings", json=booking_payload) for _ in range(5)),
    )

    new_bookings = [r for r in results[1:] if r.status_code == 201]
    assert len(new_bookings) <= 1
    async with SessionLocal() as session:
        held = await session.scalar(
            select(func.coalesce(func.sum(Booking.party_size), 0)).where(
                Booking.status == BookingStatus.PENDING
            )
        )
    assert held <= 1
