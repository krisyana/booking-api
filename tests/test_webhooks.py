import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BookingStatus, PaymentEvent
from tests.conftest import post_payment_event, seed_slot


async def _book(client, slot_id, booking_payload, party_size=1):
    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": party_size}
    )
    return response.json()


async def test_payment_confirms_the_booking(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await _book(client, slot_id, booking_payload, party_size=2)

    response = await post_payment_event(
        client, event_id="evt_1", booking_id=booking["id"], amount_idr=200_000
    )

    assert response.json() == {"result": "confirmed"}
    assert (await client.get(f"/bookings/{booking['id']}")).json()["status"] == "confirmed"


async def test_webhook_with_a_bad_signature_is_rejected(client, booking_payload):
    slot_id = await seed_slot()
    booking = await _book(client, slot_id, booking_payload)

    response = await post_payment_event(
        client, event_id="evt_x", booking_id=booking["id"], amount_idr=100_000, secret="wrong"
    )

    assert response.status_code == 401
    assert (await client.get(f"/bookings/{booking['id']}")).json()["status"] == "pending"


async def test_repeated_event_id_is_a_noop(client, booking_payload):
    slot_id = await seed_slot()
    booking = await _book(client, slot_id, booking_payload)
    args = {"event_id": "evt_dup", "booking_id": booking["id"], "amount_idr": 100_000}

    first = await post_payment_event(client, **args)
    second = await post_payment_event(client, **args)

    assert first.json()["result"] == "confirmed"
    assert second.json()["result"] == "duplicate"


async def test_simultaneous_deliveries_of_one_event_are_processed_once(client, booking_payload):
    slot_id = await seed_slot()
    booking = await _book(client, slot_id, booking_payload)

    responses = await asyncio.gather(
        *(
            post_payment_event(
                client, event_id="evt_race", booking_id=booking["id"], amount_idr=100_000
            )
            for _ in range(10)
        )
    )

    results = sorted(r.json()["result"] for r in responses)
    assert results.count("confirmed") == 1
    assert results.count("duplicate") == 9
    async with SessionLocal() as session:
        stored = await session.scalar(select(func.count()).select_from(PaymentEvent))
    assert stored == 1


async def test_wrong_amount_does_not_confirm_and_flags_a_refund(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await _book(client, slot_id, booking_payload)

    response = await post_payment_event(
        client, event_id="evt_short", booking_id=booking["id"], amount_idr=50_000
    )

    assert response.json()["result"] == "amount_mismatch"
    after = (await client.get(f"/bookings/{booking['id']}")).json()
    assert after["status"] == "pending"
    assert after["refund_due"] is True


async def test_payment_after_cancellation_flags_a_refund(client, booking_payload):
    slot_id = await seed_slot()
    booking = await _book(client, slot_id, booking_payload)
    await client.post(f"/bookings/{booking['id']}/cancel")

    response = await post_payment_event(
        client, event_id="evt_late", booking_id=booking["id"], amount_idr=100_000
    )

    assert response.json()["result"] == "refund_due"
    after = (await client.get(f"/bookings/{booking['id']}")).json()
    assert after["status"] == BookingStatus.CANCELLED
    assert after["refund_due"] is True


async def test_failed_payment_cancels_a_pending_booking(client, booking_payload):
    slot_id = await seed_slot(capacity=1)
    booking = await _book(client, slot_id, booking_payload)

    await post_payment_event(
        client, event_id="evt_fail", booking_id=booking["id"], amount_idr=0, status="failed"
    )

    assert (await client.get(f"/bookings/{booking['id']}")).json()["status"] == "cancelled"
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 201


async def test_unknown_booking_is_acknowledged_not_retried(client):
    response = await post_payment_event(
        client,
        event_id="evt_ghost",
        booking_id="00000000-0000-0000-0000-000000000000",
        amount_idr=1,
    )
    assert response.status_code == 200
    assert response.json()["result"] == "unknown_booking"
