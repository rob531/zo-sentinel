from collections.abc import Awaitable
from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_api_key_expiry

router = APIRouter(prefix="/api")


@router.get("/orgs/{org_id}/api-keys/expiry")
async def org_api_key_expiry(
    org_id: int,
    session: Session = Depends(get_session),
) -> Any:
    result: Any = get_org_api_key_expiry(org_id=org_id, session=session)
    if isinstance(result, Awaitable):
        result = await result
    return result