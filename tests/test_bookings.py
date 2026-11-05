import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Booking, utcnow
from tests.conftest import book, make_operator, seed_slot, token


async def test_booking_holds_seats_and_prices_the_party(client, booking_payload):
    slot_id = await seed_slot(capacity=4, price_idr=150_000)

    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 2}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["total_idr"] == 300_000
    assert body["paid_idr"] == 0
    assert body["refund_due_idr"] == 0
    assert body["access_token"]


async def test_available_seats_drop_as_bookings_are_held(client, booking_payload):
    operator_id, key = await make_operator()
    headers = {"X-API-Key": key}
    activity = (await client.post("/activities", json={"name": "Dive"}, headers=headers)).json()
    slot = (
        await client.post(
            f"/activities/{activity['id']}/slots",
            json={
                "starts_at": (utcnow() + timedelta(days=1)).isoformat(),
                "capacity": 6,
                "price_idr": 200_000,
            },
            headers=headers,
        )
    ).json()
    assert slot["seats_available"] == 6

    await book(client, slot["id"], booking_payload, party_size=4)

    listed = (await client.get(f"/activities/{activity['id']}/slots")).json()
    assert listed[0]["seats_available"] == 2


async def test_booking_more_than_the_remaining_seats_is_rejected(client, booking_payload):
    slot_id = await seed_slot(capacity=3)
    await book(client, slot_id, booking_payload, party_size=2)

    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 2}
    )

    assert response.status_code == 409
    assert "1 left" in response.json()["detail"]


async def test_cannot_book_a_slot_that_already_started(client, booking_payload):
    slot_id = await seed_slot(starts_in=timedelta(minutes=-5))

    response = await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)

    assert response.status_code == 409


async def test_unknown_slot_is_404(client, booking_payload):
    response = await client.post(
        "/slots/00000000-0000-0000-0000-000000000000/bookings", json=booking_payload
    )
    assert response.status_code == 404


async def test_invalid_whatsapp_number_is_rejected(client, booking_payload):
    slot_id = await seed_slot()
    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "customer_whatsapp": "not-a-number"}
    )
    assert response.status_code == 422


async def test_cancelling_frees_the_seats(client, booking_payload):
    slot_id = await seed_slot(capacity=1)
    first = await book(client, slot_id, booking_payload)
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 409

    cancelled = await client.post(f"/bookings/{first['id']}/cancel", headers=token(first))

    assert cancelled.json()["status"] == "cancelled"
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 201


# --- who may see or change a booking -------------------------------------------------------


async def test_a_booking_can_only_be_read_with_its_own_token(client, booking_payload):
    slot_id = await seed_slot(capacity=5)
    mine = await book(client, slot_id, booking_payload)
    other = await book(client, slot_id, booking_payload, customer_name="Someone Else")

    assert (await client.get(f"/bookings/{mine['id']}", headers=token(mine))).status_code == 200
    assert (await client.get(f"/bookings/{mine['id']}")).status_code == 404
    assert (
        await client.get(f"/bookings/{mine['id']}", headers={"X-Booking-Token": "wrong"})
    ).status_code == 404
    # Another customer's valid token does not open my booking.
    assert (await client.get(f"/bookings/{mine['id']}", headers=token(other))).status_code == 404


async def test_a_non_ascii_token_is_a_wrong_token_not_a_server_error(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)
    headers = {"X-Booking-Token": "é".encode()}

    assert (await client.get(f"/bookings/{booking['id']}", headers=headers)).status_code == 404
    unknown = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/bookings/{unknown}", headers=headers)).status_code == 404


async def test_a_booking_cannot_be_cancelled_without_its_token(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    assert (await client.post(f"/bookings/{booking['id']}/cancel")).status_code == 404

    still = await client.get(f"/bookings/{booking['id']}", headers=token(booking))
    assert still.json()["status"] == "pending"


# --- retrying a booking request ------------------------------------------------------------


async def test_repeating_a_request_with_the_same_idempotency_key_returns_the_same_booking(
    client, booking_payload
):
    slot_id = await seed_slot(capacity=5)
    headers = {"Idempotency-Key": "tap-1"}

    first = await client.post(f"/slots/{slot_id}/bookings", json=booking_payload, headers=headers)
    second = await client.post(f"/slots/{slot_id}/bookings", json=booking_payload, headers=headers)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    # The retry must be able to get the token even though it never saw the first response.
    assert first.json()["access_token"] == second.json()["access_token"]
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Booking)) == 1


async def test_the_same_key_with_a_different_request_is_rejected(client, booking_payload):
    slot_id = await seed_slot(capacity=5)
    headers = {"Idempotency-Key": "tap-2"}
    await client.post(f"/slots/{slot_id}/bookings", json=booking_payload, headers=headers)

    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 3}, headers=headers
    )

    assert response.status_code == 422


async def test_simultaneous_retries_with_one_key_create_one_booking(client, booking_payload):
    slot_id = await seed_slot(capacity=5)

    responses = await asyncio.gather(
        *(
            client.post(
                f"/slots/{slot_id}/bookings",
                json=booking_payload,
                headers={"Idempotency-Key": "double-tap"},
            )
            for _ in range(10)
        )
    )

    assert {r.status_code for r in responses} <= {200, 201}
    assert len({r.json()["id"] for r in responses}) == 1
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Booking)) == 1


async def test_an_idempotency_key_is_scoped_to_one_slot(client, booking_payload):
    slot_a = await seed_slot()
    slot_b = await seed_slot()
    headers = {"Idempotency-Key": "same-key"}

    a = await client.post(f"/slots/{slot_a}/bookings", json=booking_payload, headers=headers)
    b = await client.post(f"/slots/{slot_b}/bookings", json=booking_payload, headers=headers)

    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]


# --- operators -----------------------------------------------------------------------------


async def test_operator_endpoints_need_a_valid_api_key(client):
    assert (await client.post("/activities", json={"name": "Tour"})).status_code == 401
    bad = await client.post("/activities", json={"name": "Tour"}, headers={"X-API-Key": "nope"})
    assert bad.status_code == 401


async def test_operators_cannot_touch_each_others_activities(client):
    _, key_a = await make_operator("A")
    _, key_b = await make_operator("B")
    activity = (
        await client.post("/activities", json={"name": "A's tour"}, headers={"X-API-Key": key_a})
    ).json()

    response = await client.post(
        f"/activities/{activity['id']}/slots",
        json={
            "starts_at": (utcnow() + timedelta(days=1)).isoformat(),
            "capacity": 5,
            "price_idr": 1,
        },
        headers={"X-API-Key": key_b},
    )

    assert response.status_code == 404


async def test_only_the_owner_can_see_a_slot_roster(client, booking_payload):
    _, key_a = await make_operator("A")
    _, key_b = await make_operator("B")
    headers_a = {"X-API-Key": key_a}
    activity = (await client.post("/activities", json={"name": "Tour"}, headers=headers_a)).json()
    slot = (
        await client.post(
            f"/activities/{activity['id']}/slots",
            json={
                "starts_at": (utcnow() + timedelta(days=1)).isoformat(),
                "capacity": 5,
                "price_idr": 1,
            },
            headers=headers_a,
        )
    ).json()
    await book(client, slot["id"], booking_payload)

    owner_view = await client.get(f"/slots/{slot['id']}/bookings", headers=headers_a)
    other_view = await client.get(f"/slots/{slot['id']}/bookings", headers={"X-API-Key": key_b})

    assert len(owner_view.json()) == 1
    assert other_view.status_code == 404
