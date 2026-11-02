from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_score_summary


router = APIRouter()


class OrgScoreSummary(BaseModel):
    org_id: int
    org_name: str
    member_count: int = Field(ge=0)
    member_roles: Dict[str, int] = Field(default_factory=dict)
    servers_by_tier: Dict[str, int] = Field(default_factory=dict)
    mean_trust_score: Optional[float] = None
    min_trust_score: Optional[float] = None
    max_trust_score: Optional[float] = None
    trusted_pct: float = 0.0


@router.get(
    "/orgs/{org_id}/score-summary",
    response_model=OrgScoreSummary,
    summary="Get an organization's intelligence score summary",
)
def org_score_summary(
    org_id: int = Path(..., ge=1),
    session: Session = Depends(get_session),
) -> Any:
    summary = get_org_score_summary(session, org_id)

    if summary is None:
        return JSONResponse(
            status_code=404,
            content={"error": "org_not_found"},
        )

    return summary


if __name__ == "__main__":
    assert router is not None
    assert any(
        getattr(route, "path", None) == "/orgs/{org_id}/score-summary"
        for route in router.routes
    )
    print("PASS")