"""Customer notifications.

PLACEHOLDER: only `ConsoleNotifier` exists (it logs). A real WhatsApp adapter implements
`Notifier.send`; WhatsApp business messages need pre-approved templates, so `TEMPLATES` below is
the list of messages the app will need approved. See docs/mvp.md.
"""

import logging
from typing import Any, Protocol

from app.config import settings

logger = logging.getLogger("notifications")

TEMPLATES: dict[str, str] = {
    "booking_held": (
        "Your {party_size} seat(s) are held until {hold_expires_at}. Total IDR {total_idr}."
    ),
    "booking_confirmed": "Payment received. Your booking for {party_size} is confirmed.",
    "booking_cancelled": "Your booking was cancelled. Refund due: IDR {refund_due_idr}.",
    "refund_due": (
        "We received IDR {paid_idr} for your booking. IDR {refund_due_idr} of it will be refunded."
    ),
}


class Notifier(Protocol):
    async def send(self, *, message_id: str, to: str, kind: str, data: dict[str, Any]) -> None:
        """Send one message. Delivery is at-least-once, so use `message_id` to deduplicate."""
        ...


class ConsoleNotifier:
    async def send(self, *, message_id: str, to: str, kind: str, data: dict[str, Any]) -> None:
        logger.info("[%s] to=%s %s", message_id, to, TEMPLATES[kind].format(**data))


def get_notifier() -> Notifier:
    if settings.notifier == "console":
        return ConsoleNotifier()
    raise NotImplementedError(
        f"notifier '{settings.notifier}' is not implemented yet (see docs/mvp.md)"
    )
