"""Payment provider boundary.

PLACEHOLDER: only the in-memory `FakeProvider` exists. A real adapter (for example Xendit or
Midtrans) implements `PaymentProvider.create_charge` and the matching webhook mapping; the rest
of the app does not change. See docs/mvp.md for what a real adapter needs.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.config import settings


@dataclass(frozen=True)
class Charge:
    reference: str  # the provider's id for this charge
    payment_url: str  # where the customer pays (invoice page, QRIS link, ...)


class PaymentProvider(Protocol):
    async def create_charge(
        self, *, booking_id: uuid.UUID, amount_idr: int, expires_at: datetime, description: str
    ) -> Charge: ...


class FakeProvider:
    """Development stand-in. Nothing is charged; pay with `python -m app.cli simulate-payment`."""

    async def create_charge(
        self, *, booking_id: uuid.UUID, amount_idr: int, expires_at: datetime, description: str
    ) -> Charge:
        reference = f"fake_{booking_id.hex[:12]}"
        return Charge(
            reference=reference, payment_url=f"{settings.public_base_url}/fake-pay/{reference}"
        )


def get_payment_provider() -> PaymentProvider:
    if settings.payment_provider == "fake":
        return FakeProvider()
    raise NotImplementedError(
        f"payment provider '{settings.payment_provider}' is not implemented yet (see docs/mvp.md)"
    )
