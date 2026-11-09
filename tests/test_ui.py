"""The UI is served by the API, and the dev payment page confirms a booking end to end."""

from tests.conftest import book, seed_slot, token


async def test_ui_and_root_redirect(client):
    assert (await client.get("/ui/")).status_code == 200
    assert (await client.get("/ui/app.js")).status_code == 200
    assert (await client.get("/")).status_code == 307


async def test_paying_through_the_dev_page_confirms_the_booking(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload, party_size=2)
    charge = (await client.post(f"/bookings/{booking['id']}/pay", headers=token(booking))).json()
    ref = charge["reference"]

    page = await client.get(f"/fake-pay/{ref}")
    assert page.status_code == 307 and booking["id"] in page.headers["location"]

    done = await client.post(f"/fake-pay/{ref}/confirm")
    assert done.status_code == 200 and done.json()["result"] == "confirmed"
    seen = (await client.get(f"/bookings/{booking['id']}", headers=token(booking))).json()
    assert seen["status"] == "confirmed" and seen["paid_idr"] == 200_000


async def test_unknown_payment_reference_is_404(client):
    assert (await client.post("/fake-pay/nope/confirm")).status_code == 404
