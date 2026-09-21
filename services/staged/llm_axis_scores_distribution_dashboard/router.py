from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_llm_axis_scores_distribution, AxisScoreDistributionResponse

router = APIRouter(prefix="/api", tags=["llm_axis_scores_distribution_dashboard"])


@router.get(
    "/llm_axis_scores/distribution",
    response_model=AxisScoreDistributionResponse,
    summary="Get distribution of LLM axis scores over the last 30 days",
)
def llm_axis_scores_distribution(
    session: Session = Depends(get_session),
) -> AxisScoreDistributionResponse:
    """
    Retrieve the count of LLM axis scores for each axis over the past 30 days.
    """
    return get_llm_axis_scores_distribution(session)