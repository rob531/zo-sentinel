from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    case,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


SERVICE_NAME = "axis_escalation_stats_api"
AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

metadata = MetaData()

McpLlmAxisScore = Table(
    "mcp_llm_axis_scores",
    metadata,
    Column("server_id", String, nullable=False),
    Column("axis_name", String, nullable=False),
    Column("label", String),
    Column("label_index", Integer),
    Column("p_top", Float),
    Column("p_critical", Float),
    Column("p_danger", Float),
    Column("escalated", Boolean, nullable=False, default=False),
    Column("escalated_to", String),
    Column("scored_at", DateTime, nullable=False),
    Column("decision_rule_version", String),
)


class AxisEscalationStats(BaseModel):
    axis_name: str
    total: int
    escalated: int
    pct_escalated: float
    avg_p_top: float
    avg_p_critical: float
    avg_p_danger: float
    escalated_to: list[str] = Field(default_factory=list)


class EscalationTotals(BaseModel):
    total: int
    escalated: int


class EscalationStatsResponse(BaseModel):
    generated_at: str
    days: int
    axes: list[AxisEscalationStats]
    totals: EscalationTotals


def _find_real_data_dependency() -> Optional[Any]:
    """Use the service data layer when it is available in the deployment."""

    module_names = (
        "services.staged.axis_escalation_stats_api.data",
        "services.staged.axis_escalation_stats_api.db",
        "services.staged.axis_escalation_stats_api.repository",
    )
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError):
            continue

        for attribute_name in ("get_db", "get_session", "get_connection"):
            dependency = getattr(module, attribute_name, None)
            if dependency is not None:
                return dependency
    return None


_real_get_db = _find_real_data_dependency()

_fallback_engine: Optional[Engine] = None


def _get_fallback_engine() -> Engine:
    global _fallback_engine
    if _fallback_engine is None:
        _fallback_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return _fallback_engine


def _fallback_get_db() -> Iterator[Session]:
    engine = _get_fallback_engine()
    metadata.create_all(engine, checkfirst=True)
    with Session(engine) as session:
        yield session


def get_db() -> Iterator[Session]:
    if _real_get_db is not None:
        dependency_result = _real_get_db()
        if hasattr(dependency_result, "__iter__") and not hasattr(
            dependency_result, "__next__"
        ):
            yield from dependency_result
        else:
            yield dependency_result
        return
    yield from _fallback_get_db()


app = FastAPI(title=SERVICE_NAME)


@app.get(
    "/api/axes/escalation-stats",
    response_model=EscalationStatsResponse,
)
def get_escalation_stats(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_db),
) -> EscalationStatsResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    aggregate_stmt = (
        select(
            McpLlmAxisScore.c.axis_name,
            func.count().label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (McpLlmAxisScore.c.escalated.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("escalated"),
            func.avg(McpLlmAxisScore.c.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.c.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.c.p_danger).label("avg_p_danger"),
        )
        .where(McpLlmAxisScore.c.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.c.axis_name)
    )

    aggregate_rows = db.execute(aggregate_stmt).mappings().all()

    labels_stmt = (
        select(
            McpLlmAxisScore.c.axis_name,
            McpLlmAxisScore.c.escalated_to,
        )
        .where(
            McpLlmAxisScore.c.scored_at >= cutoff,
            McpLlmAxisScore.c.escalated.is_(True),
            McpLlmAxisScore.c.escalated_to.is_not(None),
        )
        .distinct()
        .order_by(
            McpLlmAxisScore.c.axis_name,
            McpLlmAxisScore.c.escalated_to,
        )
    )

    labels_by_axis: dict[str, list[str]] = {axis: [] for axis in AXIS_NAMES}
    for row in db.execute(labels_stmt).mappings():
        axis_name = row["axis_name"]
        label = row["escalated_to"]
        if axis_name in labels_by_axis and label is not None:
            labels_by_axis[axis_name].append(str(label))

    aggregates: dict[str, dict[str, Any]] = {}
    for row in aggregate_rows:
        axis_name = str(row["axis_name"])
        total = int(row["total"] or 0)
        escalated = int(row["escalated"] or 0)
        aggregates[axis_name] = {
            "total": total,
            "escalated": escalated,
            "pct_escalated": round((escalated / total) * 100, 2) if total else 0.0,
            "avg_p_top": float(row["avg_p_top"] or 0.0),
            "avg_p_critical": float(row["avg_p_critical"] or 0.0),
            "avg_p_danger": float(row["avg_p_danger"] or 0.0),
        }

    axes: list[AxisEscalationStats] = []
    for axis_name in AXIS_NAMES:
        values = aggregates.get(
            axis_name,
            {
                "total": 0,
                "escalated": 0,
                "pct_escalated": 0.0,
                "avg_p_top": 0.0,
                "avg_p_critical": 0.0,
                "avg_p_danger": 0.0,
            },
        )
        axes.append(
            AxisEscalationStats(
                axis_name=axis_name,
                total=values["total"],
                escalated=values["escalated"],
                pct_escalated=values["pct_escalated"],
                avg_p_top=values["avg_p_top"],
                avg_p_critical=values["avg_p_critical"],
                avg_p_danger=values["avg_p_danger"],
                escalated_to=labels_by_axis.get(axis_name, []),
            )
        )

    totals = EscalationTotals(
        total=sum(axis.total for axis in axes),
        escalated=sum(axis.escalated for axis in axes),
    )

    return EscalationStatsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        days=days,
        axes=axes,
        totals=totals,
    )


def _seed_test_data(engine: Engine) -> None:
    metadata.create_all(engine, checkfirst=True)
    now = datetime.utcnow()

    with engine.begin() as connection:
        connection.execute(
            McpLlmAxisScore.insert(),
            [
                {
                    "server_id": "server-auth-1",
                    "axis_name": "auth_strength",
                    "label": "weak",
                    "label_index": 1,
                    "p_top": 0.80,
                    "p_critical": 0.60,
                    "p_danger": 0.20,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": now,
                    "decision_rule_version": "v1",
                },
                {
                    "server_id": "server-auth-2",
                    "axis_name": "auth_strength",
                    "label": "critical",
                    "label_index": 4,
                    "p_top": 0.95,
                    "p_critical": 0.90,
                    "p_danger": 0.40,
                    "escalated": True,
                    "escalated_to": "critical",
                    "scored_at": now,
                    "decision_rule_version": "v1",
                },
                {
                    "server_id": "server-overall-1",
                    "axis_name": "overall_risk",
                    "label": "low",
                    "label_index": 0,
                    "p_top": 0.25,
                    "p_critical": 0.05,
                    "p_danger": 0.01,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": now,
                    "decision_rule_version": "v1",
                },
            ],
        )


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _seed_test_data(engine)

    def override_get_db() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/axes/escalation-stats")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 30
            assert isinstance(payload["generated_at"], str)
            assert payload["totals"]["total"] == 3
            assert payload["totals"]["escalated"] == 1

            axes_by_name = {axis["axis_name"]: axis for axis in payload["axes"]}
            assert set(axes_by_name) == set(AXIS_NAMES)

            auth_strength = axes_by_name["auth_strength"]
            assert auth_strength["total"] == 2
            assert auth_strength["escalated"] == 1
            assert auth_strength["escalated_to"] == ["critical"]

            overall_risk = axes_by_name["overall_risk"]
            assert overall_risk["total"] == 1
            assert overall_risk["escalated"] == 0

            for axis_name in AXIS_NAMES:
                assert axes_by_name[axis_name]["total"] >= 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

    print("PASS")


def run_contract() -> None:
    run_self_test()


def main() -> None:
    run_self_test()


if __name__ == "__main__":
    main()