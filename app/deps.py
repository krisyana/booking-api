from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Operator
from app.security import hash_api_key

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
