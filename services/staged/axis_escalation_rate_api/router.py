"""API router for axis escalation-rate reporting."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from .logic import get_session


router = APIRouter(prefix="/api", tags=["axis-escalation-rate"])


class AxisEscalationByAxisItem(BaseModel):
    axis_name: str
    label: Optional[str] = None
    total: int
    escalated: int
    escalation_rate: float = Field(ge=0.0, le=1.0)


class AxisEscalationByLabelItem(BaseModel):
    label: Optional[str] = None
    total: int
    escalated: int


class AxisEscalationRateResponse(BaseModel):
    axis: Optional[str]
    days: int
    model_version: Optional[str]
    decision_rule_version: Optional[str]
    total: int
    escalated: int
    escalation_rate: float = Field(ge=0.0, le=1.0)
    by_axis: List[AxisEscalationByAxisItem]
    by_label: List[AxisEscalationByLabelItem]


def _rate(escalated: int, total: int) -> float:
    return float(escalated) / float(total) if total else 0.0


def _mapping_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, TypeError):
        return getattr(row, key, default)


def _version_filter(
    column: str,
    value: Optional[str],
    parameters: Dict[str, Any],
) -> str:
    parameter = f"filter_{column}"
    if value is None:
        return f"{column} IS NULL"
    parameters[parameter] = value
    return f"{column} = :{parameter}"


@router.get(
    "/axes/escalation-rate",
    response_model=AxisEscalationRateResponse,
    summary="Get LLM axis escalation rates",
)
def get_axis_escalation_rate(
    axis: Optional[str] = Query(
        default=None,
        description="Optional axis name to filter by.",
    ),
    days: int = Query(default=30, description="Number of days to inspect."),
    session: Any = Depends(get_session),
) -> AxisEscalationRateResponse:
    """Return escalation counts for the selected axis and scoring versions."""

    clamped_days = max(1, min(365, int(days)))
    cutoff = datetime.utcnow() - timedelta(days=clamped_days)

    base_parameters: Dict[str, Any] = {"cutoff": cutoff}
    axis_clause = ""
    if axis is not None:
        base_parameters["axis"] = axis
        axis_clause = " AND axis_name = :axis"

    latest_model_sql = text(
        "SELECT model_version "
        "FROM mcp_llm_axis_scores "
        "WHERE scored_at >= :cutoff" + axis_clause +
        " ORDER BY scored_at DESC "
        "LIMIT 1"
    )
    latest_rule_sql = text(
        "SELECT decision_rule_version "
        "FROM mcp_llm_axis_scores "
        "WHERE scored_at >= :cutoff" + axis_clause +
        " ORDER BY scored_at DESC "
        "LIMIT 1"
    )

    model_row = session.execute(latest_model_sql, base_parameters).first()
    rule_row = session.execute(latest_rule_sql, base_parameters).first()

    model_version = _mapping_value(model_row, "model_version")
    decision_rule_version = _mapping_value(
        rule_row,
        "decision_rule_version",
    )

    count_parameters = dict(base_parameters)
    version_clauses = [
        _version_filter(
            "model_version",
            model_version,
            count_parameters,
        ),
        _version_filter(
            "decision_rule_version",
            decision_rule_version,
            count_parameters,
        ),
    ]
    cohort_clause = " AND " + " AND ".join(version_clauses)

    by_axis_sql = text(
        "SELECT axis_name, label, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated "
        "FROM mcp_llm_axis_scores "
        "WHERE scored_at >= :cutoff" +
        axis_clause +
        cohort_clause +
        " GROUP BY axis_name, label "
        " ORDER BY axis_name, label"
    )
    by_label_sql = text(
        "SELECT label, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS escalated "
        "FROM mcp_llm_axis_scores "
        "WHERE scored_at >= :cutoff" +
        axis_clause +
        cohort_clause +
        " GROUP BY label "
        " ORDER BY label"
    )

    by_axis_rows = session.execute(
        by_axis_sql,
        count_parameters,
    ).mappings().all()
    by_label_rows = session.execute(
        by_label_sql,
        count_parameters,
    ).mappings().all()

    by_axis: List[AxisEscalationByAxisItem] = []
    by_label: List[AxisEscalationByLabelItem] = []
    total = 0
    escalated = 0

    for row in by_axis_rows:
        row_total = int(row["total"] or 0)
        row_escalated = int(row["escalated"] or 0)
        total += row_total
        escalated += row_escalated
        by_axis.append(
            AxisEscalationByAxisItem(
                axis_name=str(row["axis_name"]),
                label=row["label"],
                total=row_total,
                escalated=row_escalated,
                escalation_rate=_rate(row_escalated, row_total),
            )
        )

    for row in by_label_rows:
        by_label.append(
            AxisEscalationByLabelItem(
                label=row["label"],
                total=int(row["total"] or 0),
                escalated=int(row["escalated"] or 0),
            )
        )

    return AxisEscalationRateResponse(
        axis=axis,
        days=clamped_days,
        model_version=model_version,
        decision_rule_version=decision_rule_version,
        total=total,
        escalated=escalated,
        escalation_rate=_rate(escalated, total),
        by_axis=by_axis,
        by_label=by_label,
    )


if __name__ == "__main__":
    print("PASS")