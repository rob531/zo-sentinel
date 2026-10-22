from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_axis_rule_version_drift as _get_axis_rule_version_drift
except ImportError:
    try:
        from .logic import (
            get_axis_decision_rule_version_drift as _get_axis_rule_version_drift,
        )
    except ImportError:
        try:
            from .logic import get_rule_version_drift as _get_axis_rule_version_drift
        except ImportError:
            from .logic import get_axis_drift as _get_axis_rule_version_drift


router = APIRouter(prefix="/api", tags=["axis"])


class AxisDrift(BaseModel):
    axis_name: str
    distinct_rule_versions: List[Optional[str]] = Field(default_factory=list)
    distinct_model_versions: List[Optional[str]] = Field(default_factory=list)
    distinct_adapter_sha256: List[Optional[str]] = Field(default_factory=list)
    rows: int
    latest_scored_at: datetime
    drift_detected: bool


class AxisRuleVersionDriftResponse(BaseModel):
    days: int
    generated_at: datetime
    axes: List[AxisDrift]


def _axis_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "_mapping"):
        return dict(value._mapping)
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    return {
        "axis_name": getattr(value, "axis_name"),
        "distinct_rule_versions": getattr(value, "distinct_rule_versions"),
        "distinct_model_versions": getattr(value, "distinct_model_versions"),
        "distinct_adapter_sha256": getattr(value, "distinct_adapter_sha256"),
        "rows": getattr(value, "rows"),
        "latest_scored_at": getattr(value, "latest_scored_at"),
        "drift_detected": getattr(value, "drift_detected"),
    }


@router.get(
    "/axis/rule-version-drift",
    response_model=AxisRuleVersionDriftResponse,
    summary="Report decision-rule and adapter drift by axis",
)
def get_rule_version_drift(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> AxisRuleVersionDriftResponse:
    result = _get_axis_rule_version_drift(session, days)

    if isinstance(result, dict) and "axes" in result:
        axis_rows = result["axes"]
    elif isinstance(result, (list, tuple)):
        axis_rows = result
    else:
        axis_rows = [result]

    return AxisRuleVersionDriftResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        axes=[AxisDrift(**_axis_dict(row)) for row in axis_rows],
    )