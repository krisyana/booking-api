"""Development-only stand-in for the payment provider's hosted page (FakeProvider).

Mounted only when PAYMENT_PROVIDER=fake and ENVIRONMENT=development. "Paying" here sends the same
event a real provider would, through the normal payment service.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.deps import SessionDep
from app.models import Booking
from app.schemas import WebhookResult
from app.services.payments import apply_payment_event

router = APIRouter(tags=["dev"], include_in_schema=False)


async def _booking_for(reference: str, session: SessionDep) -> Booking:
    booking = await session.scalar(select(Booking).where(Booking.payment_reference == reference))
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment reference")
    return booking


@router.get("/fake-pay/{reference}")
async def fake_pay_page(reference: str, session: SessionDep):
    booking = await _booking_for(reference, session)
    return RedirectResponse(f"/ui/pay.html?ref={reference}&booking={booking.id}")


@router.post("/fake-pay/{reference}/confirm", response_model=WebhookResult)
async def fake_pay_confirm(reference: str, session: SessionDep):
    booking = await _booking_for(reference, session)
    booking_id, due = booking.id, booking.total_idr - booking.paid_idr
    await session.rollback()
    async with session.begin():
        outcome = await apply_payment_event(
            session,
            event_id=f"fake_{uuid.uuid4().hex[:12]}",
            booking_id=booking_id,
            status="paid",
            amount_idr=due,
        )
    return WebhookResult(result=outcome.value)
