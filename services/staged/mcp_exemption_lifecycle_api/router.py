from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

if __package__:
    from .logic import get_exemption_lifecycle
else:  # pragma: no cover - supports running this file as a self-test.
    from services.staged.mcp_exemption_lifecycle_api.logic import (
        get_exemption_lifecycle,
    )


class ExemptionLifecycleRow(BaseModel):
    server_id: int
    name: str
    scope: str
    granted_at: datetime
    expires_at: Optional[datetime]
    days_remaining: Optional[int]
    status: str


class ExemptionLifecycleResponse(BaseModel):
    total: int
    active: int
    expiring_soon: int
    expired: int
    by_type: Dict[str, int]
    rows: List[ExemptionLifecycleRow]


router = APIRouter(prefix="/api", tags=["mcp-exemptions"])


@router.get(
    "/exemptions/lifecycle",
    response_model=ExemptionLifecycleResponse,
    summary="Get the MCP exemption lifecycle rollup",
)
def exemption_lifecycle(
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Number of days before expiry that counts as expiring soon.",
    ),
    session: Session = Depends(get_session),
) -> ExemptionLifecycleResponse:
    result = get_exemption_lifecycle(session=session, days=days)
    return ExemptionLifecycleResponse.model_validate(result)


if __name__ == "__main__":
    route_paths = {route.path for route in router.routes}
    assert "/api/exemptions/lifecycle" in route_paths
    print("PASS")