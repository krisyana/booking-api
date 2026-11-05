import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Booking, Operator
from app.security import hash_api_key, verify_booking_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def current_operator(
    session: SessionDep, api_key: Annotated[str | None, Depends(_api_key_header)]
) -> Operator:
    if api_key:
        operator = await session.scalar(
            select(Operator).where(Operator.api_key_hash == hash_api_key(api_key))
        )
        if operator is not None:
            return operator
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")


OperatorDep = Annotated[Operator, Depends(current_operator)]


async def customer_booking(
    booking_id: uuid.UUID,
    session: SessionDep,
    x_booking_token: Annotated[str | None, Header()] = None,
) -> Booking:
    """The booking, if the caller holds its token. Wrong token and unknown id look the same."""
    booking = await session.get(Booking, booking_id)
    if booking is None or not verify_booking_token(
        booking.id, x_booking_token, settings.token_secret
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
    return booking


CustomerBookingDep = Annotated[Booking, Depends(customer_booking)]
