"""Logic for the score freshness backlog service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class FreshnessBacklogServer(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    state: Literal["missing", "stale"]
    last_scored_at: Optional[str]
    age_hours: Optional[float]


class FreshnessBacklogResponse(BaseModel):
    threshold_hours: int
    total_servers: int
    missing_count: int
    stale_count: int
    servers: List[FreshnessBacklogServer]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_freshness_backlog(
    db: Session,
    max_age_hours: int = 24,
    limit: int = 100,
    state: Optional[Literal["missing", "stale"]] = None,
) -> Dict:
    """Return registry servers whose overall risk score is missing or stale."""
    if max_age_hours < 1 or max_age_hours > 720:
        raise ValueError("max_age_hours must be between 1 and 720")
    if limit < 1:
        raise ValueError("limit must be positive")
    if state is not None and state not in ("missing", "stale"):
        raise ValueError("state must be 'missing' or 'stale'")

    score_summary = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.count(McpLlmAxisScore.id).label("score_count"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            score_summary.c.last_scored_at,
            score_summary.c.score_count,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            score_summary,
            score_summary.c.server_id == McpServerRegistry.server_id,
        )
    ).all()

    now = _utc_now()
    servers: List[FreshnessBacklogServer] = []
    missing_count = 0
    stale_count = 0

    for row in rows:
        score_count = int(row.score_count or 0)
        last_scored_at = row.last_scored_at
        age_hours: Optional[float] = None

        if last_scored_at is not None:
            age_hours = (
                (now - _normalise_datetime(last_scored_at)).total_seconds() / 3600.0
            )

        if score_count == 0:
            server_state = "missing"
            missing_count += 1
        elif age_hours is not None and age_hours > max_age_hours:
            server_state = "stale"
            stale_count += 1
        else:
            continue

        if state is not None and server_state != state:
            continue

        servers.append(
            FreshnessBacklogServer(
                server_id=row.server_id,
                name=row.name,
                registry_source=row.registry_source,
                risk_tier=row.risk_tier,
                state=server_state,
                last_scored_at=(
                    _normalise_datetime(last_scored_at).isoformat()
                    if last_scored_at is not None
                    else None
                ),
                age_hours=age_hours,
            )
        )

    servers.sort(
        key=lambda server: (
            server.age_hours is None,
            -(server.age_hours if server.age_hours is not None else 0.0),
            server.server_id,
        )
    )

    return {
        "threshold_hours": max_age_hours,
        "total_servers": len(rows),
        "missing_count": missing_count,
        "stale_count": stale_count,
        "servers": servers[:limit],
    }


router = APIRouter(prefix="/api")


@router.get(
    "/scores/freshness-backlog",
    response_model=FreshnessBacklogResponse,
)
def score_freshness_backlog(
    max_age_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1),
    state: Optional[Literal["missing", "stale"]] = Query(default=None),
    db: Session = Depends(get_session),
) -> Dict:
    return get_freshness_backlog(
        db=db,
        max_age_hours=max_age_hours,
        limit=limit,
        state=state,
    )


def build_service() -> FastAPI:
    application = FastAPI(title="score_freshness_backlog")
    application.include_router(router)
    return application


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh server",
                    registry_source="test",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale server",
                    registry_source="test",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="missing-server",
                    name="Missing server",
                    registry_source="test",
                    risk_tier="medium",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="fresh-server",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now - timedelta(hours=2),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="stale-server",
                    axis_name="overall_risk",
                    model_version="test",
                    scored_at=now - timedelta(hours=100),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = build_service()
    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get(
        "/api/scores/freshness-backlog",
        params={"max_age_hours": 24},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 3
    assert payload["missing_count"] == 1
    assert payload["stale_count"] == 1
    assert payload["servers"][0]["server_id"] == "stale-server"
    assert payload["servers"][0]["age_hours"] > 24

    response = client.get(
        "/api/scores/freshness-backlog",
        params={"max_age_hours": 24, "state": "missing"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["servers"]) == 1
    assert payload["servers"][0]["server_id"] == "missing-server"

    print("PASS")