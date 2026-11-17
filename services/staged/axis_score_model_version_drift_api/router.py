from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_axis_score_model_version_drift

router = APIRouter(prefix="/api", tags=["axis-score-model-version-drift"])


class AxisDriftStatistics(BaseModel):
    row_count: int = 0
    mean_p_top: Optional[float] = None
    min_p_top: Optional[float] = None
    max_p_top: Optional[float] = None


class AxisModelVersionDriftProfile(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: Optional[str] = None
    row_count: int = 0
    distinct_servers: int = 0
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None
    axes: Dict[str, AxisDriftStatistics] = Field(default_factory=dict)
    escalated_count: int = 0
    escalated_to_counts: Dict[str, int] = Field(default_factory=dict)


class AxisModelVersionDriftResponse(BaseModel):
    current_version: Optional[str] = None
    stale_versions: List[str] = Field(default_factory=list)
    versions: List[AxisModelVersionDriftProfile] = Field(default_factory=list)


@router.get(
    "/axis/model-version-drift",
    response_model=AxisModelVersionDriftResponse,
    status_code=200,
)
def get_axis_score_model_version_drift_endpoint(
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_axis_score_model_version_drift(session)