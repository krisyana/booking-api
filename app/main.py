import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.background import run_periodically
from app.config import settings
from app.db import SessionLocal
from app.integrations.notifications import get_notifier
from app.integrations.payments import get_payment_provider
from app.routers import activities, bookings, dev, webhooks
from app.services.bookings import expire_holds
from app.services.outbox import deliver_pending

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Misconfiguration should stop the app at startup, not surface on a customer's first payment.
    get_payment_provider()
    notifier = get_notifier()

    async def expire() -> None:
        async with SessionLocal() as session, session.begin():
            await expire_holds(session)

    async def deliver() -> None:
        async with SessionLocal() as session, session.begin():
            await deliver_pending(session, notifier)

    tasks = [
        asyncio.create_task(
            run_periodically("expire-holds", settings.expiry_interval_seconds, expire)
        ),
        asyncio.create_task(
            run_periodically("deliver-outbox", settings.outbox_interval_seconds, deliver)
        ),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Booking API",
    description="Capacity-safe bookings and idempotent payment webhooks for activity operators.",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(activities.router)
app.include_router(bookings.router)
app.include_router(webhooks.router)
if settings.payment_provider == "fake" and settings.environment == "development":
    app.include_router(dev.router)  # stand-in payment page; never mounted in production

app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
