"""HTTP surface for unauthenticated-access exposure reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_unauth_access_exposure
except ImportError:
    try:
        from .logic import list_unauth_access_exposure as get_unauth_access_exposure
    except ImportError:
        from .logic import fetch_unauth_access_exposure as get_unauth_access_exposure

router = APIRouter(
    prefix="/api/exposure/unauth-access",
    tags=["exposure"],
)


class UnauthAccessServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    url: Optional[str] = None
    source: Optional[str] = None
    updated_at: Optional[datetime] = None


class UnauthAccessGroup(BaseModel):
    unauth_access: str
    count: int
    servers: list[UnauthAccessServer]


class UnauthAccessExposureResponse(BaseModel):
    total: int
    by_access: list[UnauthAccessGroup]


def _response_from_result(result: Any) -> UnauthAccessExposureResponse:
    if isinstance(result, dict):
        raw_groups = result.get("by_access", [])
        total = result.get("total")
    elif isinstance(result, tuple) and len(result) == 2:
        total, raw_groups = result
    else:
        total = None
        raw_groups = result or []

    groups: list[UnauthAccessGroup] = []
    for group in raw_groups:
        if isinstance(group, dict):
            groups.append(UnauthAccessGroup(**group))
        else:
            groups.append(UnauthAccessGroup.model_validate(group))

    if total is None:
        total = sum(group.count for group in groups)

    return UnauthAccessExposureResponse(total=int(total), by_access=groups)


@router.get("", response_model=UnauthAccessExposureResponse)
def get_unauth_access(
    db: Session = Depends(get_session),
) -> UnauthAccessExposureResponse:
    return _response_from_result(get_unauth_access_exposure(db))