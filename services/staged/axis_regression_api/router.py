"""FastAPI surface for axis-score regression and drift analysis."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import axis_regression_analysis as _logic_axis_regression_analysis
    from .logic import axis_regression_summary as _logic_axis_regression_summary
except ImportError:
    _logic_axis_regression_analysis = None
    _logic_axis_regression_summary = None


router = APIRouter(prefix="/api", tags=["axis-regression"])

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
_AXIS_SET = frozenset(AXIS_NAMES)


class ModelVersionAggregate(BaseModel):
    model_version: str
    samples: int
    mean_p_top: float


class LabelAggregate(BaseModel):
    label: str
    samples: int
    mean_p_top: float


class DailySeriesPoint(BaseModel):
    date: str
    samples: int
    mean_p_top: float
    escalated: int


class TopEscalation(BaseModel):
    server_id: str
    name: str
    escalated_to: str
    p_top: float
    scored_at: str


class AxisRegressionResponse(BaseModel):
    axis_name: str
    days: int
    samples: int
    servers_scored: int
    mean_p_top: float
    p50_p_top: float
    p95_p_top: float
    escalation_rate: float
    by_model_version: list[ModelVersionAggregate]
    by_label: list[LabelAggregate]
    series: list[DailySeriesPoint]
    top_escalations: list[TopEscalation]


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _date_string(value: datetime | None) -> str:
    normalised = _normalise_datetime(value)
    return normalised.date().isoformat() if normalised else ""


def _iso_string(value: datetime | None) -> str:
    normalised = _normalise_datetime(value)
    return normalised.isoformat() if normalised else ""


def _empty_response(axis_name: str, days: int) -> dict[str, Any]:
    return {
        "axis_name": axis_name,
        "days": days,
        "samples": 0,
        "servers_scored": 0,
        "mean_p_top": 0.0,
        "p50_p_top": 0.0,
        "p95_p_top": 0.0,
        "escalation_rate": 0.0,
        "by_model_version": [],
        "by_label": [],
        "series": [],
        "top_escalations": [],
    }


def _analyse_axis(
    db: Session,
    axis_name: str,
    days: int,
) -> dict[str, Any]:
    cutoff = _utc_now() - timedelta(days=days)
    rows = list(
        db.execute(
            select(McpLlmAxisScore).where(McpLlmAxisScore.axis_name == axis_name)
        )
        .scalars()
        .all()
    )

    selected: list[tuple[McpLlmAxisScore, datetime]] = []
    for row in rows:
        scored_at = _normalise_datetime(row.scored_at)
        if scored_at is not None and scored_at >= cutoff:
            selected.append((row, scored_at))

    if not selected:
        return _empty_response(axis_name, days)

    p_values = [
        value
        for row, _ in selected
        if (value := _as_float(row.p_top)) is not None
    ]
    escalated_count = sum(1 for row, _ in selected if bool(row.escalated))

    model_groups: dict[str, list[float]] = defaultdict(list)
    model_counts: dict[str, int] = defaultdict(int)
    label_groups: dict[str, list[float]] = defaultdict(list)
    label_counts: dict[str, int] = defaultdict(int)
    daily_groups: dict[str, list[float]] = defaultdict(list)
    daily_counts: dict[str, int] = defaultdict(int)
    daily_escalated: dict[str, int] = defaultdict(int)

    for row, scored_at in selected:
        p_top = _as_float(row.p_top)
        model_version = str(row.model_version or "")
        label = str(row.label or "")
        day = scored_at.date().isoformat()

        model_counts[model_version] += 1
        label_counts[label] += 1
        daily_counts[day] += 1
        if bool(row.escalated):
            daily_escalated[day] += 1
        if p_top is not None:
            model_groups[model_version].append(p_top)
            label_groups[label].append(p_top)
            daily_groups[day].append(p_top)

    server_ids = {str(row.server_id) for row, _ in selected}
    registry_rows = list(db.execute(select(McpServerRegistry)).scalars().all())
    registry = {str(row.server_id): row for row in registry_rows}

    escalation_rows: list[tuple[float, datetime, McpLlmAxisScore]] = []
    for row, scored_at in selected:
        if not bool(row.escalated):
            continue
        escalation_rows.append(
            (_as_float(row.p_top) or 0.0, scored_at, row)
        )
    escalation_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)

    top_escalations = []
    for p_top, scored_at, row in escalation_rows[:10]:
        registry_row = registry.get(str(row.server_id))
        top_escalations.append(
            {
                "server_id": str(row.server_id),
                "name": str(
                    getattr(registry_row, "name", None)
                    or getattr(registry_row, "server_id", None)
                    or row.server_id
                ),
                "escalated_to": str(row.escalated_to or ""),
                "p_top": p_top,
                "scored_at": _iso_string(scored_at),
            }
        )

    return {
        "axis_name": axis_name,
        "days": days,
        "samples": len(selected),
        "servers_scored": len(server_ids),
        "mean_p_top": _mean(p_values),
        "p50_p_top": _percentile(p_values, 0.50),
        "p95_p_top": _percentile(p_values, 0.95),
        "escalation_rate": escalated_count / len(selected),
        "by_model_version": [
            {
                "model_version": model_version,
                "samples": model_counts[model_version],
                "mean_p_top": _mean(model_groups[model_version]),
            }
            for model_version in sorted(model_counts)
        ],
        "by_label": [
            {
                "label": label,
                "samples": label_counts[label],
                "mean_p_top": _mean(label_groups[label]),
            }
            for label in sorted(label_counts)
        ],
        "series": [
            {
                "date": day,
                "samples": daily_counts[day],
                "mean_p_top": _mean(daily_groups[day]),
                "escalated": daily_escalated[day],
            }
            for day in sorted(daily_counts)
        ],
        "top_escalations": top_escalations,
    }


def axis_regression_analysis(db: Session, axis_name: str, days: int) -> dict[str, Any]:
    if _logic_axis_regression_analysis is not None:
        try:
            result = _logic_axis_regression_analysis(db, axis_name, days)
            if isinstance(result, dict):
                return result
        except TypeError:
            pass
    return _analyse_axis(db, axis_name, days)


def axis_regression_summary(db: Session, days: int) -> list[dict[str, Any]]:
    if _logic_axis_regression_summary is not None:
        try:
            result = _logic_axis_regression_summary(db, days)
            if isinstance(result, list):
                return result
        except TypeError:
            pass
    return [_analyse_axis(db, axis_name, days) for axis_name in AXIS_NAMES]


@router.get(
    "/axis/{axis_name}/regression",
    response_model=AxisRegressionResponse,
)
def get_axis_regression(
    axis_name: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> Any:
    if axis_name not in _AXIS_SET:
        return JSONResponse(status_code=400, content={"error": "unknown_axis"})
    return axis_regression_analysis(db, axis_name, days)


@router.get(
    "/axis/regression/summary",
    response_model=list[AxisRegressionResponse],
)
def get_axis_regression_summary(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> Any:
    return axis_regression_summary(db, days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    server_ids = ["server-a", "server-b", "server-c"]
    axes = ["overall_risk", "auth_strength"]
    row_id = 1
    for server_index, server_id in enumerate(server_ids):
        test_session.add(
            McpServerRegistry(
                server_id=server_id,
                name=f"Server {server_index + 1}",
                risk_tier="medium",
                trust_score=50.0,
                registry_source="selftest",
                last_assessed=now,
            )
        )
        for axis_index, axis_name in enumerate(axes):
            for day_index in range(2):
                test_session.add(
                    McpLlmAxisScore(
                        id=row_id,
                        server_id=server_id,
                        axis_name=axis_name,
                        label="high" if axis_index == 0 else "normal",
                        label_index=axis_index,
                        p_top=10.0 + server_index * 5.0 + day_index,
                        p_critical=5.0,
                        p_danger=10.0,
                        escalated=(row_id == 1),
                        escalated_to="security" if row_id == 1 else None,
                        decision_rule_version="v1",
                        model_version="model-a" if row_id % 2 else "model-b",
                        scored_at=now - timedelta(days=day_index),
                        probs=None,
                    )
                )
                row_id += 1
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/axis/overall_risk/regression?days=30")
    assert response.status_code == 200
    payload = response.json()
    expected_keys = {
        "axis_name",
        "days",
        "samples",
        "servers_scored",
        "mean_p_top",
        "p50_p_top",
        "p95_p_top",
        "escalation_rate",
        "by_model_version",
        "by_label",
        "series",
        "top_escalations",
    }
    assert expected_keys <= payload.keys()
    assert payload["samples"] == 6
    assert len(payload["series"]) == 2
    assert 0.0 <= payload["escalation_rate"] <= 1.0
    assert 0.0 <= payload["mean_p_top"] <= 100.0

    invalid = client.get("/api/axis/not_an_axis/regression")
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "unknown_axis"}

    print("PASS")