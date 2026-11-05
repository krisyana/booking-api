import asyncio

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.integrations.notifications import ConsoleNotifier
from app.models import OutboxMessage
from app.services.outbox import deliver_pending
from tests.conftest import book, post_payment_event, seed_slot, token


class RecordingNotifier:
    def __init__(self, fail_times: int = 0, delay: float = 0.0):
        self.sent: list[tuple[str, str, str]] = []  # (message_id, to, kind)
        self.fail_times = fail_times
        self.delay = delay

    async def send(self, *, message_id, to, kind, data):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("provider is down")
        self.sent.append((message_id, to, kind))


async def _deliver(notifier) -> int:
    async with SessionLocal() as session, session.begin():
        return await deliver_pending(session, notifier)


async def _messages() -> list[OutboxMessage]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at)))


async def test_creating_a_booking_queues_a_hold_message_for_the_customer(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)

    await book(client, slot_id, booking_payload, party_size=2)

    [message] = await _messages()
    assert (message.kind, message.recipient) == (
        "booking_held",
        booking_payload["customer_whatsapp"],
    )
    assert message.payload["total_idr"] == 200_000
    assert message.sent_at is None


async def test_a_rejected_booking_queues_nothing(client, booking_payload):
    slot_id = await seed_slot(capacity=1)
    await client.post(f"/slots/{slot_id}/bookings", json={**booking_payload, "party_size": 5})

    assert await _messages() == []


async def test_confirmation_and_overpayment_queue_their_own_messages(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)

    await post_payment_event(client, event_id="evt_o", booking_id=booking["id"], amount_idr=120_000)

    kinds = [m.kind for m in await _messages()]
    assert kinds == ["booking_held", "booking_confirmed", "refund_due"]


async def test_cancelling_queues_a_cancellation_message(client, booking_payload):
    slot_id = await seed_slot()
    booking = await book(client, slot_id, booking_payload)

    await client.post(f"/bookings/{booking['id']}/cancel", headers=token(booking))

    assert [m.kind for m in await _messages()] == ["booking_held", "booking_cancelled"]


async def test_each_message_is_delivered_once(client, booking_payload):
    slot_id = await seed_slot()
    await book(client, slot_id, booking_payload)
    notifier = RecordingNotifier()

    first = await _deliver(notifier)
    second = await _deliver(notifier)

    assert (first, second) == (1, 0)
    assert len(notifier.sent) == 1


async def test_two_workers_never_send_the_same_message(client, booking_payload):
    slot_id = await seed_slot(capacity=20)
    for _ in range(8):
        await book(client, slot_id, booking_payload)
    notifier = RecordingNotifier(delay=0.02)

    await asyncio.gather(_deliver(notifier), _deliver(notifier), _deliver(notifier))

    ids = [message_id for message_id, _, _ in notifier.sent]
    assert len(ids) == 8
    assert len(set(ids)) == 8  # no message went out twice


async def test_a_failing_send_is_retried_and_then_succeeds(client, booking_payload):
    slot_id = await seed_slot()
    await book(client, slot_id, booking_payload)
    notifier = RecordingNotifier(fail_times=2)

    assert await _deliver(notifier) == 0
    assert await _deliver(notifier) == 0
    assert await _deliver(notifier) == 1

    [message] = await _messages()
    assert message.sent_at is not None
    assert message.attempts == 2


async def test_a_message_that_keeps_failing_is_given_up_on(client, booking_payload):
    slot_id = await seed_slot()
    await book(client, slot_id, booking_payload)
    notifier = RecordingNotifier(fail_times=1000)

    for _ in range(settings.outbox_max_attempts + 3):
        await _deliver(notifier)

    [message] = await _messages()
    assert message.sent_at is None
    assert message.attempts == settings.outbox_max_attempts


async def test_the_console_notifier_renders_every_template(client, booking_payload):
    slot_id = await seed_slot(price_idr=100_000)
    booking = await book(client, slot_id, booking_payload)
    await post_payment_event(client, event_id="evt_c", booking_id=booking["id"], amount_idr=120_000)
    await client.post(f"/bookings/{booking['id']}/cancel", headers=token(booking))

    async with SessionLocal() as session, session.begin():
        sent = await deliver_pending(session, ConsoleNotifier())

    assert sent == len(await _messages())  # nothing raised while formatting any message
