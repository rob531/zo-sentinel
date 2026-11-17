from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import summarize_escalations as _summarize_escalations
except ImportError:
    try:
        from .logic import get_escalation_summary as _summarize_escalations
    except ImportError:
        try:
            from .logic import summarize_axis_escalations as _summarize_escalations
        except ImportError:
            try:
                from .logic import get_axis_escalation_summary as _summarize_escalations
            except ImportError:
                from .logic import build_escalation_summary as _summarize_escalations


router = APIRouter(prefix="/api")


class EscalationTargetCount(BaseModel):
    target: str
    count: int


class AxisEscalationSummary(BaseModel):
    axis_name: str
    escalated_count: int
    escalated_to: List[EscalationTargetCount]
    avg_p_critical: float
    avg_p_top: float
    row_count: int


class EscalationSummaryResponse(BaseModel):
    axes: List[AxisEscalationSummary]
    rule_version_drift_rows: int


def _as_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, BaseModel):
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        return payload.dict()
    if isinstance(payload, dict):
        return payload
    raise TypeError("Escalation summary logic returned an unsupported payload")


@router.get(
    "/scoring/escalations",
    response_model=EscalationSummaryResponse,
    summary="Summarize LLM axis escalation behaviour",
)
def get_escalation_summary(
    session: Session = Depends(get_session),
) -> EscalationSummaryResponse:
    payload = _as_dict(_summarize_escalations(session))
    return EscalationSummaryResponse.parse_obj(payload)


if __name__ == "__main__":
    print("PASS")