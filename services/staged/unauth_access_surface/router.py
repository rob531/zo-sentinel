"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Delegates to the service's logic module,
injecting a real DB session via Depends(get_session). All intra‑service
imports are relative so the module works identically in staged and active
service directories.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# Import the service's core logic (implementation lives in logic.py)
from .logic import unauth_access_surface

router = APIRouter(prefix="/api", tags=["unauth_access_surface"])


class UnauthAccessItem(BaseModel):
    server_id: str
    url: str
    unauth_access: str
    source: str
    updated_at: datetime


class UnauthAccessResponse(BaseModel):
    total: int
    exposed: int
    items: List[UnauthAccessItem]


@router.get(
    "/security/unauth-access",
    response_model=UnauthAccessResponse,
    summary="List unauthenticated access surface",
)
def get_unauth_access(
    server_id: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_session),
) -> UnauthAccessResponse:
    """
    Retrieve rows from the `mcp_unauth_access` table indicating exposed
    endpoints. Optional query parameters filter by `server_id` and/or `source`.

    The underlying logic returns a mapping with keys:
        - total: total rows examined
        - exposed: rows matching the exposure criteria
        - items: list of rows (as dicts) matching the filters
    """
    result = unauth_access_surface(db, server_id=server_id, source=source)
    # `result` is expected to be a dict compatible with UnauthAccessResponse
    return UnauthAccessResponse(**result)