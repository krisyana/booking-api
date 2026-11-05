import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import PaymentEvent
from tests.conftest import book, post_payment_event, seed_slot, token


async def _state(client, booking):
    return (await client.get(f"/bookings/{booking['id']}", headers=token(booking))).json()


async def test_payment_confirms_the_booking(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload, party_size=2)

    response = await post_payment_event(
        client, event_id="evt_1", booking_id=booking["id"], amount_idr=200_000
    )

    assert response.json() == {"result": "confirmed"}
    state = await _state(client, booking)
    assert state["status"] == "confirmed"
    assert (state["paid_idr"], state["refund_due_idr"]) == (200_000, 0)


async def test_webhook_with_a_bad_signature_is_rejected(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    response = await post_payment_event(
        client, event_id="evt_x", booking_id=booking["id"], amount_idr=100_000, secret="wrong"
    )

    assert response.status_code == 401
    assert (await _state(client, booking))["status"] == "pending"


async def test_repeated_event_id_is_a_noop(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)
    args = {"event_id": "evt_dup", "booking_id": booking["id"], "amount_idr": 100_000}

    first = await post_payment_event(client, **args)
    second = await post_payment_event(client, **args)

    assert first.json()["result"] == "confirmed"
    assert second.json()["result"] == "duplicate"
    # The duplicate must not be counted as a second payment.
    assert (await _state(client, booking))["paid_idr"] == 100_000


async def test_simultaneous_deliveries_of_one_event_are_processed_once(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

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
        assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 1
    assert (await _state(client, booking))["paid_idr"] == 100_000


# --- money that does not match the total ---------------------------------------------------


async def test_a_partial_payment_keeps_the_hold_until_the_rest_arrives(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload, party_size=2)  # total 200_000

    first = await post_payment_event(
        client, event_id="evt_part_1", booking_id=booking["id"], amount_idr=50_000
    )
    after_first = await _state(client, booking)
    second = await post_payment_event(
        client, event_id="evt_part_2", booking_id=booking["id"], amount_idr=150_000
    )

    assert first.json()["result"] == "partial"
    assert (after_first["status"], after_first["paid_idr"]) == ("pending", 50_000)
    assert after_first["refund_due_idr"] == 0  # not owed back: it is still being completed
    assert second.json()["result"] == "confirmed"
    assert (await _state(client, booking))["status"] == "confirmed"


async def test_an_overpayment_confirms_and_records_the_excess_as_refund_due(
    client, booking_payload
):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)

    response = await post_payment_event(
        client, event_id="evt_over", booking_id=booking["id"], amount_idr=130_000
    )

    assert response.json()["result"] == "confirmed"
    state = await _state(client, booking)
    assert state["status"] == "confirmed"
    assert (state["paid_idr"], state["refund_due_idr"]) == (130_000, 30_000)


async def test_paying_a_confirmed_booking_again_under_a_new_event_is_refund_due(
    client, booking_payload
):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_a", booking_id=booking["id"], amount_idr=100_000)

    again = await post_payment_event(
        client, event_id="evt_b", booking_id=booking["id"], amount_idr=100_000
    )

    assert again.json()["result"] == "overpaid"
    state = await _state(client, booking)
    assert (state["status"], state["refund_due_idr"]) == ("confirmed", 100_000)


async def test_payment_after_cancellation_is_refund_due(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await client.post(f"/bookings/{booking['id']}/cancel", headers=token(booking))

    response = await post_payment_event(
        client, event_id="evt_late", booking_id=booking["id"], amount_idr=100_000
    )

    assert response.json()["result"] == "refund_due"
    state = await _state(client, booking)
    assert (state["status"], state["refund_due_idr"]) == ("cancelled", 100_000)


async def test_cancelling_a_paid_booking_makes_everything_paid_refund_due(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_p", booking_id=booking["id"], amount_idr=100_000)

    cancelled = await client.post(f"/bookings/{booking['id']}/cancel", headers=token(booking))

    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["refund_due_idr"] == 100_000


async def test_failed_payment_cancels_a_pending_booking(client, booking_payload):
    slot_id = await seed_slot(capacity=1)
    booking = await book(client, slot_id, booking_payload)

    await post_payment_event(
        client, event_id="evt_fail", booking_id=booking["id"], amount_idr=0, status="failed"
    )

    assert (await _state(client, booking))["status"] == "cancelled"
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 201


async def test_failed_payment_does_not_cancel_a_booking_that_was_partly_paid(
    client, booking_payload
):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_h", booking_id=booking["id"], amount_idr=40_000)

    await post_payment_event(
        client, event_id="evt_f", booking_id=booking["id"], amount_idr=0, status="failed"
    )

    assert (await _state(client, booking))["status"] == "pending"


async def test_unknown_booking_is_acknowledged_not_retried(client):
    response = await post_payment_event(
        client,
        event_id="evt_ghost",
        booking_id="00000000-0000-0000-0000-000000000000",
        amount_idr=1,
    )
    assert response.status_code == 200
    assert response.json()["result"] == "unknown_booking"
