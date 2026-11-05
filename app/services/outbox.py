"""Transactional outbox for customer notifications.

`enqueue` runs inside the caller's transaction, so a notification exists exactly when the booking
change it announces was committed. `deliver_pending` sends them later. Several workers can run at
once: rows are claimed with SKIP LOCKED, so each message goes to one worker at a time.

Delivery is at-least-once. If the process dies after a send but before the commit, the message is
sent again, which is why the notifier receives a stable `message_id`.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.notifications import Notifier
from app.models import Booking, OutboxMessage, utcnow

logger = logging.getLogger(__name__)


def enqueue(session: AsyncSession, kind: str, booking: Booking) -> None:
    session.add(
        OutboxMessage(
            kind=kind,
            recipient=booking.customer_whatsapp,
            payload={
                "booking_id": str(booking.id),
                "party_size": booking.party_size,
                "total_idr": booking.total_idr,
                "paid_idr": booking.paid_idr,
                "refund_due_idr": booking.refund_due_idr,
                "hold_expires_at": booking.hold_expires_at.isoformat(),
            },
        )
    )


async def deliver_pending(
    session: AsyncSession, notifier: Notifier, *, limit: int = 50, now: datetime | None = None
) -> int:
    """Send up to `limit` pending messages. Returns how many were sent."""
    now = now or utcnow()
    messages = (
        await session.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.sent_at.is_(None),
                OutboxMessage.attempts < settings.outbox_max_attempts,
            )
            .order_by(OutboxMessage.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()

    sent = 0
    for message in messages:
        try:
            await notifier.send(
                message_id=str(message.id),
                to=message.recipient,
                kind=message.kind,
                data=message.payload,
            )
        except Exception:
            message.attempts += 1
            logger.exception(
                "sending %s %s failed (attempt %s)", message.kind, message.id, message.attempts
            )
        else:
            message.sent_at = now
            sent += 1
    return sent
