from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, Optional, Union

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


KNOWN_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class ServerIdentity(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None


class AxisEvidence(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    probs: Dict[str, Any] = Field(default_factory=dict)
    scored_at: Optional[datetime] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None


class AxisBreakdownResponse(BaseModel):
    server: ServerIdentity
    axes: list[AxisEvidence] = Field(default_factory=list)
    missing_axes: list[str] = Field(default_factory=list)
    criteria_version: Optional[str] = None


AxisEvidenceBreakdown = AxisEvidence
AxisBreakdownResult = AxisBreakdownResponse


@contextmanager
def _session_scope() -> Iterator[Session]:
    session_context = get_session()
    entered = False
    session: Any

    if hasattr(session_context, "execute"):
        session = session_context
    elif hasattr(session_context, "__enter__"):
        session = session_context.__enter__()
        entered = True
    else:
        session = next(session_context)

    try:
        yield session
    finally:
        if entered:
            session_context.__exit__(None, None, None)
        else:
            try:
                session.close()
            finally:
                close = getattr(session_context, "close", None)
                if close is not None:
                    close()


def _decode_probs(value: Any) -> Dict[str, Any]:
    decoded = value

    if isinstance(decoded, str):
        for _ in range(2):
            try:
                decoded = json.loads(decoded)
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            if not isinstance(decoded, str):
                break

    if isinstance(decoded, dict):
        return decoded
    return {}


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _axis_evidence(row: Any) -> AxisEvidence:
    return AxisEvidence(
        axis_name=str(getattr(row, "axis_name")),
        label=_optional_string(getattr(row, "label", None)),
        label_index=_optional_int(getattr(row, "label_index", None)),
        p_top=_optional_float(getattr(row, "p_top", None)),
        p_critical=_optional_float(getattr(row, "p_critical", None)),
        p_danger=_optional_float(getattr(row, "p_danger", None)),
        escalated=bool(getattr(row, "escalated", False)),
        escalated_to=_optional_string(getattr(row, "escalated_to", None)),
        probs=_decode_probs(getattr(row, "probs", None)),
        scored_at=getattr(row, "scored_at", None),
        decision_rule_version=_optional_string(
            getattr(row, "decision_rule_version", None)
        ),
        model_version=_optional_string(getattr(row, "model_version", None)),
    )


def get_axis_breakdown(server_id: Union[str, int]) -> AxisBreakdownResponse:
    requested_server_id = str(server_id)

    with _session_scope() as session:
        registry_row = (
            session.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == server_id)
            .first()
        )
        if registry_row is None:
            registry_row = (
                session.query(McpServerRegistry)
                .filter(McpServerRegistry.server_id == requested_server_id)
                .first()
            )

        score_rows = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == server_id)
            .all()
        )
        if not score_rows:
            score_rows = (
                session.query(McpLlmAxisScore)
                .filter(McpLlmAxisScore.server_id == requested_server_id)
                .all()
            )

    rows_by_axis: Dict[str, Any] = {}
    for row in score_rows:
        axis_name = getattr(row, "axis_name", None)
        if axis_name in KNOWN_AXES:
            rows_by_axis[str(axis_name)] = row

    axes = [
        _axis_evidence(rows_by_axis[axis_name])
        for axis_name in KNOWN_AXES
        if axis_name in rows_by_axis
    ]
    missing_axes = [
        axis_name for axis_name in KNOWN_AXES if axis_name not in rows_by_axis
    ]

    server = ServerIdentity(
        server_id=requested_server_id,
        name=_optional_string(getattr(registry_row, "name", None)),
        verdict=_optional_string(getattr(registry_row, "verdict", None)),
        risk_tier=_optional_string(getattr(registry_row, "risk_tier", None)),
        trust_score=_optional_float(getattr(registry_row, "trust_score", None)),
    )

    overall_row = rows_by_axis.get("overall_risk")
    criteria_version = (
        _optional_string(getattr(overall_row, "decision_rule_version", None))
        if overall_row is not None
        else None
    )

    return AxisBreakdownResponse(
        server=server,
        axes=axes,
        missing_axes=missing_axes,
        criteria_version=criteria_version,
    )


get_axis_evidence_breakdown = get_axis_breakdown
get_axis_evidence = get_axis_breakdown
get_breakdown = get_axis_breakdown


if __name__ == "__main__":
    assert len(KNOWN_AXES) == 7
    assert KNOWN_AXES[0] == "overall_risk"
    assert KNOWN_AXES[-1] == "exploit_surface"
    assert _decode_probs('{"overall_risk": 0.75}') == {"overall_risk": 0.75}
    assert _decode_probs({"overall_risk": 0.75}) == {"overall_risk": 0.75}
    assert _decode_probs("not-json") == {}
    assert _decode_probs(None) == {}
    print("PASS")