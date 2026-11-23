from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore as AxisScoreModel
    from app.models import McpServerRegistry as ServerRegistryModel
except ImportError:
    try:
        from app.models import McpLlmAxisScore as AxisScoreModel
        from app.models import McpServerRegistry as ServerRegistryModel
    except ImportError:
        try:
            from app.models import McpLlmAxisScore as AxisScoreModel
            from app.models import McpServerRegistry as ServerRegistryModel
        except ImportError:
            from app.models import McpLlmAxisScore as AxisScoreModel
            from app.models import McpServerRegistry as ServerRegistryModel

try:
    from .logic import AXIS_NAMES as LOGIC_AXIS_NAMES
except ImportError:
    LOGIC_AXIS_NAMES = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )


router = APIRouter(prefix="/api", tags=["axis-score-freshness"])

_REQUIRED_AXIS_NAMES: Tuple[str, ...] = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

AXIS_NAMES: Tuple[str, ...] = tuple(
    axis_name
    for axis_name in _REQUIRED_AXIS_NAMES
    if axis_name in set(LOGIC_AXIS_NAMES)
)
if len(AXIS_NAMES) != len(_REQUIRED_AXIS_NAMES):
    AXIS_NAMES = _REQUIRED_AXIS_NAMES


class FreshnessBuckets(BaseModel):
    lt_1h: int = 0
    lt_24h: int = 0
    lt_7d: int = 0
    gt_7d: int = 0


class AxisFreshness(BaseModel):
    axis_name: str
    scored_servers: int = 0
    buckets: FreshnessBuckets = Field(default_factory=FreshnessBuckets)
    mean_p_top: Optional[float] = None
    model_versions: List[str] = Field(default_factory=list)
    decision_rule_versions: List[str] = Field(default_factory=list)


class NeverScoredServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str


class NeverScoredSummary(BaseModel):
    count: int = 0
    sample_servers: List[NeverScoredServer] = Field(default_factory=list)


class AxisFreshnessResponse(BaseModel):
    generated_at: datetime
    days: int
    axes: List[AxisFreshness] = Field(default_factory=list)
    never_scored: NeverScoredSummary = Field(default_factory=NeverScoredSummary)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _server_identifier(value: Any) -> str:
    return str(value)


def _version_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bucket_for_age(age: timedelta) -> str:
    if age < timedelta(hours=1):
        return "lt_1h"
    if age < timedelta(days=1):
        return "lt_24h"
    if age < timedelta(days=7):
        return "lt_7d"
    return "gt_7d"


def _load_registry(session: Session, limit: int) -> List[Any]:
    statement = (
        select(ServerRegistryModel)
        .order_by(ServerRegistryModel.server_id)
        .limit(max(0, limit))
    )
    return list(session.execute(statement).scalars().all())


def _load_scores(session: Session, server_ids: List[Any]) -> List[Any]:
    if not server_ids:
        return []
    statement = (
        select(AxisScoreModel)
        .where(AxisScoreModel.server_id.in_(server_ids))
        .order_by(
            AxisScoreModel.axis_name,
            AxisScoreModel.server_id,
            AxisScoreModel.scored_at.desc(),
        )
    )
    return list(session.execute(statement).scalars().all())


def build_axis_freshness(
    session: Session,
    days: int = 7,
    limit: int = 200,
    generated_at: Optional[datetime] = None,
) -> AxisFreshnessResponse:
    now = _as_utc(generated_at) if generated_at is not None else _utc_now()
    registry_rows = _load_registry(session, limit)
    server_ids = [row.server_id for row in registry_rows]
    score_rows = _load_scores(session, server_ids)

    latest_scores: Dict[str, Dict[str, Any]] = {axis_name: {} for axis_name in AXIS_NAMES}
    known_axis_names = set(AXIS_NAMES)

    for score in score_rows:
        axis_name = str(score.axis_name)
        if axis_name not in known_axis_names or score.scored_at is None:
            continue

        server_id = _server_identifier(score.server_id)
        current = latest_scores[axis_name].get(server_id)
        if current is None:
            latest_scores[axis_name][server_id] = score
            continue

        current_time = _as_utc(current.scored_at)
        candidate_time = _as_utc(score.scored_at)
        if candidate_time > current_time:
            latest_scores[axis_name][server_id] = score

    axes: List[AxisFreshness] = []

    for axis_name in AXIS_NAMES:
        axis_scores = latest_scores[axis_name]
        buckets = FreshnessBuckets()
        p_top_values: List[float] = []
        model_versions: List[str] = []
        decision_rule_versions: List[str] = []

        for score in axis_scores.values():
            if score.scored_at is None:
                continue

            age = now - _as_utc(score.scored_at)
            bucket_name = _bucket_for_age(age)
            setattr(buckets, bucket_name, getattr(buckets, bucket_name) + 1)

            if score.p_top is not None:
                p_top_values.append(float(score.p_top))

            model_version = _version_value(score.model_version)
            if model_version is not None and model_version not in model_versions:
                model_versions.append(model_version)

            decision_rule_version = _version_value(score.decision_rule_version)
            if (
                decision_rule_version is not None
                and decision_rule_version not in decision_rule_versions
            ):
                decision_rule_versions.append(decision_rule_version)

        mean_p_top = (
            sum(p_top_values) / len(p_top_values)
            if p_top_values
            else None
        )

        axes.append(
            AxisFreshness(
                axis_name=axis_name,
                scored_servers=len(axis_scores),
                buckets=buckets,
                mean_p_top=mean_p_top,
                model_versions=sorted(model_versions),
                decision_rule_versions=sorted(decision_rule_versions),
            )
        )

    servers_with_scores = {
        server_id
        for axis_name in AXIS_NAMES
        for server_id in latest_scores[axis_name]
    }
    never_scored_rows = [
        row
        for row in registry_rows
        if _server_identifier(row.server_id) not in servers_with_scores
    ]

    return AxisFreshnessResponse(
        generated_at=now,
        days=days,
        axes=axes,
        never_scored=NeverScoredSummary(
            count=len(never_scored_rows),
            sample_servers=[
                NeverScoredServer(
                    server_id=_server_identifier(row.server_id),
                    name=str(row.name),
                    risk_tier=str(row.risk_tier),
                )
                for row in never_scored_rows
            ],
        ),
    )


@router.get("/axis/freshness", response_model=AxisFreshnessResponse)
def get_axis_freshness(
    days: int = Query(7),
    limit: int = Query(200),
    session: Session = Depends(get_session),
) -> AxisFreshnessResponse:
    return build_axis_freshness(session=session, days=days, limit=limit)


if __name__ == "__main__":
    response = AxisFreshnessResponse(
        generated_at=_utc_now(),
        days=7,
        axes=[
            AxisFreshness(
                axis_name=axis_name,
                scored_servers=0,
                buckets=FreshnessBuckets(),
                mean_p_top=None,
                model_versions=[],
                decision_rule_versions=[],
            )
            for axis_name in AXIS_NAMES
        ],
        never_scored=NeverScoredSummary(count=0, sample_servers=[]),
    )
    assert response.days == 7
    assert len(response.axes) == 7
    assert response.never_scored.count == 0
    print("PASS")