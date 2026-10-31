from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisCompleteness(BaseModel):
    axis_name: str
    scored_servers: int
    missing_servers: int
    escalated_servers: int
    mean_p_top: float


class CompletenessTotals(BaseModel):
    servers_scored: int
    axes: int
    complete_servers: int


class CompletenessResponse(BaseModel):
    days: int
    generated_at: datetime
    axes: List[AxisCompleteness]
    totals: CompletenessTotals


def calculate_axis_score_completeness(
    session: Session, days: int = 7
) -> CompletenessResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.escalated,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    window_servers: Set[str] = set()
    servers_by_axis: Dict[str, Set[str]] = {axis: set() for axis in AXIS_NAMES}
    escalated_by_axis: Dict[str, Set[str]] = {
        axis: set() for axis in AXIS_NAMES
    }
    probability_sums: Dict[str, float] = {axis: 0.0 for axis in AXIS_NAMES}
    probability_counts: Dict[str, int] = {axis: 0 for axis in AXIS_NAMES}

    for server_id, axis_name, p_top, escalated in rows:
        if server_id is None:
            continue

        server_key = str(server_id)
        window_servers.add(server_key)

        if axis_name not in servers_by_axis:
            continue

        servers_by_axis[axis_name].add(server_key)

        if escalated is True:
            escalated_by_axis[axis_name].add(server_key)

        if p_top is not None:
            probability_sums[axis_name] += float(p_top)
            probability_counts[axis_name] += 1

    axis_results: List[AxisCompleteness] = []
    for axis_name in AXIS_NAMES:
        scored_servers = len(servers_by_axis[axis_name])
        probability_count = probability_counts[axis_name]
        mean_p_top = (
            probability_sums[axis_name] / probability_count
            if probability_count
            else 0.0
        )

        axis_results.append(
            AxisCompleteness(
                axis_name=axis_name,
                scored_servers=scored_servers,
                missing_servers=len(window_servers) - scored_servers,
                escalated_servers=len(escalated_by_axis[axis_name]),
                mean_p_top=mean_p_top,
            )
        )

    complete_servers = sum(
        1
        for server_id in window_servers
        if all(server_id in servers_by_axis[axis] for axis in AXIS_NAMES)
    )

    return CompletenessResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        axes=axis_results,
        totals=CompletenessTotals(
            servers_scored=len(window_servers),
            axes=len(AXIS_NAMES),
            complete_servers=complete_servers,
        ),
    )


router = APIRouter(prefix="/api", tags=["axis-score-completeness"])


@router.get("/scores/completeness", response_model=CompletenessResponse)
def get_axis_score_completeness(
    days: int = Query(default=7, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> CompletenessResponse:
    return calculate_axis_score_completeness(session=session, days=days)


app = FastAPI(title="Axis Score Completeness")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestingSession()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-a",
                axis_name="overall_risk",
                model_version="test-model",
                p_top=0.9,
                escalated=False,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-a",
                axis_name="auth_strength",
                model_version="test-model",
                p_top=0.8,
                escalated=True,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-a",
                axis_name="exploit_surface",
                model_version="test-model",
                p_top=0.7,
                escalated=False,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=4,
                server_id="server-b",
                axis_name="auth_strength",
                model_version="test-model",
                p_top=0.6,
                escalated=False,
                scored_at=now,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scores/completeness", params={"days": 7})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 7

        axes = {axis["axis_name"]: axis for axis in payload["axes"]}
        assert axes["auth_strength"]["scored_servers"] == 2
        assert axes["exploit_surface"]["scored_servers"] == 1
        assert axes["exploit_surface"]["missing_servers"] == 1

    test_session.close()
    test_engine.dispose()
    print("PASS")