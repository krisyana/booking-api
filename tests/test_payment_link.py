"""POST /bookings/{id}/pay — the payment-provider placeholder (FakeProvider)."""

import asyncio

from sqlalchemy import update

from app.config import settings
from app.db import SessionLocal
from app.models import Booking, utcnow
from tests.conftest import book, post_payment_event, seed_slot, token


async def test_a_charge_is_created_for_the_amount_due(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload, party_size=2)

    response = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert response.status_code == 200
    body = response.json()
    assert body["amount_idr"] == 200_000
    assert body["reference"].startswith("fake_")
    assert body["payment_url"].startswith(settings.public_base_url)


async def test_asking_twice_returns_the_same_charge(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    a = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))
    b = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert a.json() == b.json()


async def test_simultaneous_taps_create_one_charge(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    responses = await asyncio.gather(
        *(client.post(f"/bookings/{booking['id']}/pay", headers=token(booking)) for _ in range(8))
    )

    assert {r.status_code for r in responses} == {200}
    assert len({r.json()["reference"] for r in responses}) == 1


async def test_the_amount_shrinks_after_a_partial_payment(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_h", booking_id=booking["id"], amount_idr=30_000)

    response = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert response.json()["amount_idr"] == 70_000


async def test_needs_the_booking_token(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    assert (await client.post(f"/bookings/{booking['id']}/pay")).status_code == 404


async def test_a_paid_booking_cannot_start_another_payment(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_p", booking_id=booking["id"], amount_idr=100_000)

    response = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert response.status_code == 409


async def test_a_lapsed_hold_cannot_start_a_payment(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)
    async with SessionLocal() as session, session.begin():
        await session.execute(update(Booking).values(hold_expires_at=utcnow()))

    response = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert response.status_code == 409


async def test_an_unimplemented_provider_answers_501(client, booking_payload, monkeypatch):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)
    monkeypatch.setattr(settings, "payment_provider", "xendit")

    response = await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))

    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"]
