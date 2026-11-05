from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.config import settings
from app.deps import SessionDep
from app.schemas import PaymentEventIn, WebhookResult
from app.security import verify_webhook
from app.services.payments import apply_payment_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payments", response_model=WebhookResult)
async def payment_webhook(
    request: Request,
    session: SessionDep,
    x_signature: str | None = Header(default=None),
):
    """Receive a payment event. The signature covers the raw body, so verify before parsing."""
    body = await request.body()
    if not verify_webhook(body, x_signature, settings.webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    try:
        event = PaymentEventIn.model_validate_json(body)
    except ValidationError as e:
        detail = e.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from e

    async with session.begin():
        outcome = await apply_payment_event(session, **event.model_dump())
    return WebhookResult(result=outcome.value)
