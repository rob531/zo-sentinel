from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
)
from .logic import get_membership_drift

router = APIRouter(prefix="/api", tags=["perspectives"])


class DriftedMember(BaseModel):
    server_id: str
    name: Optional[str] = None
    snapshot_tier: Optional[str] = None
    current_tier: Optional[str] = None
    delta: Any = None


class PerspectiveMembershipDrift(BaseModel):
    perspective_id: str
    name: str
    snapshot_taken_at: Optional[datetime] = None
    member_count: int = 0
    drifted: List[DriftedMember] = Field(default_factory=list)
    unseen_event_count: int = 0


class PerspectiveMembershipDriftResponse(BaseModel):
    generated_at: datetime
    perspectives: List[PerspectiveMembershipDrift] = Field(default_factory=list)


@router.get(
    "/perspectives/membership-drift",
    response_model=PerspectiveMembershipDriftResponse,
)
def membership_drift(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_membership_drift(session)


if __name__ == "__main__":
    sample = PerspectiveMembershipDriftResponse(
        generated_at=datetime.now(),
        perspectives=[],
    )
    assert sample.generated_at is not None
    assert sample.perspectives == []
    print("PASS")