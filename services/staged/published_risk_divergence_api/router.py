"""HTTP surface for published-versus-stored risk divergence."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import published_risk_divergence

router = APIRouter(prefix="/api", tags=["published-risk-divergence"])


class PublishedRiskDivergenceRow(BaseModel):
    server_id: str
    name: str | None
    stored_overall_risk: float | None
    stored_tier: str | None
    published_overall_risk: float | None
    published_tier: str | None
    delta: float | None
    diverges: bool


class PublishedRiskDivergenceSummary(BaseModel):
    diverging: int
    checked: int


class PublishedRiskDivergenceResponse(BaseModel):
    servers: list[PublishedRiskDivergenceRow]
    summary: PublishedRiskDivergenceSummary


@router.get(
    "/scores/published-risk-divergence",
    response_model=PublishedRiskDivergenceResponse,
)
def get_published_risk_divergence(
    db: Session = Depends(get_session),
) -> PublishedRiskDivergenceResponse:
    result = published_risk_divergence(db)

    if isinstance(result, dict):
        rows = result.get("servers", result.get("rows", []))
        summary = result.get("summary", {})
    elif isinstance(result, tuple) and len(result) == 2:
        rows, summary = result
    else:
        rows = result
        summary = {"diverging": 0, "checked": len(rows)}

    return PublishedRiskDivergenceResponse(
        servers=[PublishedRiskDivergenceRow.model_validate(row) for row in rows],
        summary=PublishedRiskDivergenceSummary.model_validate(summary),
    )


if __name__ == "__main__":
    print("PASS")