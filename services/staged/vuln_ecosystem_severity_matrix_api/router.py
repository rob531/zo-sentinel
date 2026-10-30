from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_ecosystem_severity_matrix as fetch_ecosystem_severity_matrix

router = APIRouter(prefix="/api", tags=["vulnerability"])


class EcosystemSeverityMatrix(BaseModel):
    ecosystem: str
    severity_counts: dict[str, int]
    total: int
    linked_servers: int
    avg_match_confidence: float | None


@router.get(
    "/vuln/ecosystem-severity-matrix",
    response_model=list[EcosystemSeverityMatrix],
)
def ecosystem_severity_matrix(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[EcosystemSeverityMatrix]:
    return fetch_ecosystem_severity_matrix(session=session, limit=limit)