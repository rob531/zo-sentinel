from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_ecosystem_severity_rollup


router = APIRouter(prefix="/api", tags=["vulnerability"])


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class EcosystemSeverityRollup(BaseModel):
    ecosystem: str
    severities: SeverityCounts
    linked_servers: int = 0
    avg_match_confidence: Optional[float] = None


class EcosystemSeverityRollupResponse(BaseModel):
    total_advisories: int = 0
    total_links: int = 0
    ecosystems: List[EcosystemSeverityRollup] = Field(default_factory=list)


@router.get(
    "/vuln/ecosystem-severity",
    response_model=EcosystemSeverityRollupResponse,
    summary="Roll up vulnerability advisories and linked servers by ecosystem",
)
def ecosystem_severity_rollup(
    session=Depends(get_session),
) -> EcosystemSeverityRollupResponse:
    result = get_ecosystem_severity_rollup(session)
    return EcosystemSeverityRollupResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    paths = {route.path for route in test_app.routes}
    assert "/api/vuln/ecosystem-severity" in paths
    print("PASS")