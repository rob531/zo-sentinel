"""Router for the Server Risk Assessment Detail service.

Provides a thin FastAPI layer that delegates all business logic to
`services.staged.server_risk_assessment_detail.logic`.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# Application‑wide database session provider.
from app.db import get_session

# Business‑logic entry point and response schema.
from .logic import (
    ServerRiskAssessmentDetailResponse,
    get_server_risk_assessment_detail,
)

router = APIRouter(prefix="/api", tags=["server_risk_assessment_detail"])


@router.get(
    "/servers/{server_id}/risk/assessment",
    response_model=ServerRiskAssessmentDetailResponse,
    name="Get detailed risk assessment for a server",
)
def server_risk_assessment_detail(
    server_id: int,
    db: Session = Depends(get_session),
) -> ServerRiskAssessmentDetailResponse:
    """
    Return a detailed risk assessment for the specified server.

    The heavy lifting (SQL queries, data shaping, risk calculations) is
    performed in `logic.get_server_risk_assessment_detail`.  If the server
    cannot be found, a 404 error is raised.
    """
    result = get_server_risk_assessment_detail(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result