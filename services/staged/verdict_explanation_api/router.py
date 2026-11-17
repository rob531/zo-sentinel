from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import build_explanation


class ExplanationAxis(BaseModel):
    axis_name: str
    label: str
    p_top: float
    escalated: bool
    escalated_to: Optional[str] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None


class ExplanationResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    axes: List[ExplanationAxis] = []
    criteria_versions: List[str] = []


# Backwards-compatible aliases for consumers that use a more explicit name.
AxisExplanation = ExplanationAxis
VerdictExplanationResponse = ExplanationResponse
VerdictExplanation = ExplanationResponse


router = APIRouter(prefix="/api", tags=["verdict_explanation_api"])


@router.get(
    "/servers/{server_id}/explanation",
    response_model=ExplanationResponse,
    summary="Explain a server's trust verdict",
)
def get_server_explanation(
    server_id: str,
    session: Session = Depends(get_session),
) -> ExplanationResponse:
    try:
        explanation = build_explanation(session, server_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Server not found") from exc

    if explanation is None:
        raise HTTPException(status_code=404, detail="Server not found")

    return ExplanationResponse(**explanation)


if __name__ == "__main__":
    print("PASS")