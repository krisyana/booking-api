import os

# Must happen before the app is imported so it connects to the test database.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://localhost/booking_api_test"
)

import json  # noqa: E402
import uuid  # noqa: E402
from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Activity, Base, Operator, Slot, utcnow  # noqa: E402
from app.security import new_api_key, sign_webhook  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE outbox_messages, payment_events, bookings, slots, activities, operators "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def make_operator(name: str = "Test Operator") -> tuple[uuid.UUID, str]:
    key, key_hash = new_api_key()
    async with SessionLocal() as session, session.begin():
        operator = Operator(name=name, api_key_hash=key_hash)
        session.add(operator)
    return operator.id, key


async def seed_slot(
    *, capacity: int = 5, price_idr: int = 100_000, starts_in: timedelta = timedelta(days=2)
) -> uuid.UUID:
    """Insert an operator, an activity and one slot straight into the database."""
    operator_id, _ = await make_operator()
    async with SessionLocal() as session, session.begin():
        activity = Activity(operator_id=operator_id, name="Surf lesson")
        session.add(activity)
        await session.flush()
        slot = Slot(
            activity_id=activity.id,
            starts_at=utcnow() + starts_in,
            capacity=capacity,
            price_idr=price_idr,
        )
        session.add(slot)
    return slot.id


async def book(client: AsyncClient, slot_id, payload: dict, **overrides) -> dict:
    """Create a booking and return the response body (including its access_token)."""
    response = await client.post(f"/slots/{slot_id}/bookings", json={**payload, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def token(booking: dict) -> dict[str, str]:
    """Headers a customer sends to read, cancel or pay for their booking."""
    return {"X-Booking-Token": booking["access_token"]}


@pytest.fixture
def booking_payload():
    return {"customer_name": "Made Wirawan", "customer_whatsapp": "+6281234567890", "party_size": 1}


async def post_payment_event(
    client: AsyncClient,
    *,
    event_id: str,
    booking_id: str | uuid.UUID,
    amount_idr: int,
    status: str = "paid",
    secret: str | None = None,
):
    body = json.dumps(
        {
            "event_id": event_id,
            "booking_id": str(booking_id),
            "status": status,
            "amount_idr": amount_idr,
        }
    ).encode()
    return await client.post(
        "/webhooks/payments",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sign_webhook(body, secret or settings.webhook_secret),
        },
    )
