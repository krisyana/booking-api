import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_periodically(name: str, interval_seconds: float, job: Callable[[], Awaitable[None]]):
    """Run `job` forever. A failing run is logged and retried on the next tick instead of
    silently ending the loop (a dead expiry or outbox loop would go unnoticed otherwise)."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background job %s failed; will retry", name)
