from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_first_seen_age_distribution as _get_first_seen_age_distribution
except ImportError:
    try:
        from .logic import get_first_seen_age_stats as _get_first_seen_age_distribution
    except ImportError:
        try:
            from .logic import get_registry_first_seen_age as _get_first_seen_age_distribution
        except ImportError:
            from .logic import get_first_seen_age as _get_first_seen_age_distribution


router = APIRouter(prefix="/api", tags=["registry"])


class AgeBand(BaseModel):
    band: str
    servers: int = Field(ge=0)
    never_scanned: int = Field(ge=0)
    tiers: Dict[str, int] = Field(default_factory=dict)


class FirstSeenAgeResponse(BaseModel):
    days: int
    bands: List[AgeBand]
    total_servers: int = Field(ge=0)
    generated_at: datetime


@router.get(
    "/registry/first-seen-age",
    response_model=FirstSeenAgeResponse,
    summary="Age distribution of registered MCP servers",
)
def get_first_seen_age(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> FirstSeenAgeResponse:
    payload = _get_first_seen_age_distribution(session=session, days=days)

    if isinstance(payload, BaseModel):
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        else:
            payload = payload.dict()

    payload = dict(payload)
    payload.setdefault("days", days)

    return FirstSeenAgeResponse(**payload)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    assert any(
        route.path == "/api/registry/first-seen-age"
        for route in test_app.routes
    )
    print("PASS")