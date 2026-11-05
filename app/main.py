import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import SessionLocal
from app.routers import activities, bookings, webhooks
from app.services.bookings import expire_holds


async def _expire_loop() -> None:
    while True:
        await asyncio.sleep(settings.expiry_interval_seconds)
        async with SessionLocal() as session, session.begin():
            await expire_holds(session)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_expire_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Booking API",
    description="Capacity-safe bookings and idempotent payment webhooks for activity operators.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(activities.router)
app.include_router(bookings.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
