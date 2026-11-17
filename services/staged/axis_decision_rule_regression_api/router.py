from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_decision_rule_regression as _logic_get_decision_rule_regression
except ImportError:
    try:
        from .logic import (
            get_axis_decision_rule_regression as _logic_get_decision_rule_regression,
        )
    except ImportError:
        _logic_get_decision_rule_regression = None


class DecisionRuleRegressionVersion(BaseModel):
    decision_rule_version: str
    model_version: str
    total_rows: int = Field(ge=0)
    escalated_rows: int = Field(ge=0)
    distinct_servers: int = Field(ge=0)
    axes: dict[str, int]


class DecisionRuleRegressionResponse(BaseModel):
    days: int
    versions: list[DecisionRuleRegressionVersion]


router = APIRouter(prefix="/api")


def _fetch_decision_rule_regression(
    session: Session, days: int
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    ).where(McpLlmAxisScore.scored_at >= cutoff)

    rows = session.execute(statement).all()

    grouped_rows: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        decision_rule_version = (
            "" if row.decision_rule_version is None else str(row.decision_rule_version)
        )
        model_version = "" if row.model_version is None else str(row.model_version)
        grouped_rows[(decision_rule_version, model_version)].append(row)

    versions: list[DecisionRuleRegressionVersion] = []
    for (decision_rule_version, model_version), version_rows in sorted(
        grouped_rows.items()
    ):
        axes: dict[str, int] = defaultdict(int)
        servers: set[Any] = set()
        escalated_rows = 0

        for row in version_rows:
            axis_name = "" if row.axis_name is None else str(row.axis_name)
            axes[axis_name] += 1
            if row.escalated is True:
                escalated_rows += 1
            if row.server_id is not None:
                servers.add(row.server_id)

        versions.append(
            DecisionRuleRegressionVersion(
                decision_rule_version=decision_rule_version,
                model_version=model_version,
                total_rows=len(version_rows),
                escalated_rows=escalated_rows,
                distinct_servers=len(servers),
                axes=dict(axes),
            )
        )

    return {"days": days, "versions": versions}


def _normalize_response(result: Any, days: int) -> dict[str, Any]:
    if isinstance(result, BaseModel):
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        else:
            result = result.dict()
    elif hasattr(result, "_asdict"):
        result = result._asdict()
    elif not isinstance(result, dict):
        result = {
            "days": days,
            "versions": result,
        }

    normalized = dict(result)
    normalized["days"] = normalized.get("days", days)
    normalized["versions"] = normalized.get("versions", [])
    return normalized


@router.get(
    "/scores/decision-rule-regression",
    response_model=DecisionRuleRegressionResponse,
)
def decision_rule_regression(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if _logic_get_decision_rule_regression is not None:
        result = _logic_get_decision_rule_regression(session, days)
    else:
        result = _fetch_decision_rule_regression(session, days)

    return _normalize_response(result, days)


if __name__ == "__main__":
    route_paths = {route.path for route in router.routes}
    assert "/api/scores/decision-rule-regression" in route_paths
    print("PASS")