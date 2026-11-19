"""HTTP surface for axis-score cardinality reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import get_axis_score_cardinality as _logic_axis_score_cardinality
except ImportError:
    _logic_axis_score_cardinality = None


router = APIRouter(prefix="/api/axis-score", tags=["axis-score"])

CANONICAL_AXES: list[str] = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisBucket(BaseModel):
    axis_name: str
    servers_missing: int


class AxisCardinalityServer(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    axes_present: int
    axes_missing: list[str]
    latest_scored_at: datetime | None = None
    risk_tier: str | None = None


class AxisCardinalityResponse(BaseModel):
    total_servers: int
    complete: int
    partial: int
    missing: int
    completeness_pct: float
    buckets: list[AxisBucket]
    servers: list[AxisCardinalityServer]


def _axis_score_cardinality(
    db: Session,
    limit: int = 100,
    source: str | None = None,
) -> AxisCardinalityResponse:
    registry_query = select(McpServerRegistry)
    if source:
        registry_query = registry_query.where(
            McpServerRegistry.registry_source == source
        )

    registry_rows = list(
        db.execute(registry_query.order_by(McpServerRegistry.server_id).limit(limit))
        .scalars()
        .all()
    )

    server_ids = [row.server_id for row in registry_rows]
    score_rows: list[McpLlmAxisScore] = []
    if server_ids:
        score_rows = list(
            db.execute(
                select(McpLlmAxisScore).where(
                    McpLlmAxisScore.server_id.in_(server_ids),
                    McpLlmAxisScore.axis_name.in_(CANONICAL_AXES),
                )
            )
            .scalars()
            .all()
        )

    axes_by_server: dict[str, set[str]] = {server_id: set() for server_id in server_ids}
    latest_by_server: dict[str, datetime | None] = {
        server_id: None for server_id in server_ids
    }
    for score in score_rows:
        axes_by_server.setdefault(score.server_id, set()).add(score.axis_name)
        if score.scored_at is not None:
            current = latest_by_server.get(score.server_id)
            if current is None or score.scored_at > current:
                latest_by_server[score.server_id] = score.scored_at

    servers: list[AxisCardinalityServer] = []
    missing_counts = {axis_name: 0 for axis_name in CANONICAL_AXES}
    complete = 0
    partial = 0
    missing = 0

    for registry in registry_rows:
        present = axes_by_server.get(registry.server_id, set())
        missing_axes = [axis_name for axis_name in CANONICAL_AXES if axis_name not in present]
        for axis_name in missing_axes:
            missing_counts[axis_name] += 1

        if not missing_axes:
            complete += 1
        elif present:
            partial += 1
        else:
            missing += 1

        servers.append(
            AxisCardinalityServer(
                server_id=registry.server_id,
                name=registry.name,
                registry_source=registry.registry_source,
                axes_present=len(present),
                axes_missing=missing_axes,
                latest_scored_at=latest_by_server.get(registry.server_id),
                risk_tier=registry.risk_tier,
            )
        )

    total_servers = len(servers)
    completeness_pct = round((complete / total_servers) * 100, 2) if total_servers else 0.0

    return AxisCardinalityResponse(
        total_servers=total_servers,
        complete=complete,
        partial=partial,
        missing=missing,
        completeness_pct=completeness_pct,
        buckets=[
            AxisBucket(axis_name=axis_name, servers_missing=missing_counts[axis_name])
            for axis_name in CANONICAL_AXES
        ],
        servers=servers,
    )


@router.get("/cardinality", response_model=AxisCardinalityResponse)
def get_cardinality(
    limit: int = Query(default=100, ge=1, le=500),
    source: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> AxisCardinalityResponse:
    return _axis_score_cardinality(db, limit=limit, source=source)


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
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    db = TestSession()
    db.add_all(
        [
            McpServerRegistry(
                server_id="complete",
                name="Complete server",
                registry_source="test",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="partial",
                name="Partial server",
                registry_source="test",
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="empty",
                name="Empty server",
                registry_source="test",
                risk_tier="high",
            ),
        ]
    )
    for index, axis_name in enumerate(CANONICAL_AXES):
        db.add(
            McpLlmAxisScore(
                id=index + 1,
                server_id="complete",
                axis_name=axis_name,
                model_version="test-model",
            )
        )
    for index, axis_name in enumerate(CANONICAL_AXES[:3]):
        db.add(
            McpLlmAxisScore(
                id=100 + index,
                server_id="partial",
                axis_name=axis_name,
                model_version="test-model",
            )
        )
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/axis-score/cardinality")
    payload: dict[str, Any] = response.json()
    assert response.status_code == 200
    assert payload["buckets"][6]["axis_name"] == "exploit_surface"
    assert payload["buckets"][6]["servers_missing"] == 2
    assert next(
        server for server in payload["servers"] if server["server_id"] == "complete"
    )["axes_missing"] == []
    assert payload["complete"] == 1
    assert payload["total_servers"] == 3
    assert payload["completeness_pct"] == 33.33
    print("PASS")
    db.close()
    test_engine.dispose()