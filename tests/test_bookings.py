from datetime import timedelta

from app.models import utcnow
from tests.conftest import make_operator, seed_slot


async def test_booking_holds_seats_and_prices_the_party(client, booking_payload):
    slot_id = await seed_slot(capacity=4, price_idr=150_000)

    response = await client.post(
        f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 2}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["total_idr"] == 300_000
    assert body["refund_due"] is False


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

    await client.post(f"/slots/{slot['id']}/bookings", json={**booking_payload, "party_size": 4})

    listed = (await client.get(f"/activities/{activity['id']}/slots")).json()
    assert listed[0]["seats_available"] == 2


async def test_booking_more_than_the_remaining_seats_is_rejected(client, booking_payload):
    slot_id = await seed_slot(capacity=3)
    await client.post(f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 2})

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
    first = (await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)).json()
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 409

    cancelled = await client.post(f"/bookings/{first['id']}/cancel")

    assert cancelled.json()["status"] == "cancelled"
    assert (
        await client.post(f"/slots/{slot_id}/bookings", json=booking_payload)
    ).status_code == 201


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
    await client.post(f"/slots/{slot['id']}/bookings", json=booking_payload)

    owner_view = await client.get(f"/slots/{slot['id']}/bookings", headers=headers_a)
    other_view = await client.get(f"/slots/{slot['id']}/bookings", headers={"X-API-Key": key_b})

    assert len(owner_view.json()) == 1
    assert other_view.status_code == 404
