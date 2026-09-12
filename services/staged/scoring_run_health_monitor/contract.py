from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring"])


class ScoringRunHealth(BaseModel):
    total_runs: int = Field(description="Total number of scoring runs")
    recent_runs: int = Field(description="Scoring runs in the last hour")
    anomaly_count: int = Field(description="Count of anomalies detected")
    health_status: str = Field(description="Overall health: healthy/degraded/unhealthy")
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    last_run_at: datetime | None = Field(default=None)


class HealthResponse(BaseModel):
    status: str = Field(default="ok")
    data: ScoringRunHealth


def check_scoring_run_health(session: Session) -> dict:
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)

    total_runs = session.execute(
        select(func.count(McpLlmAxisScore.id))
    ).scalar() or 0

    recent_runs = session.execute(
        select(func.count(McpLlmAxisScore.id)).where(
            McpLlmAxisScore.scored_at >= hour_ago
        )
    ).scalar() or 0

    anomaly_result = session.execute(
        select(McpLlmAxisScore).where(
            (McpLlmAxisScore.server_id.is_(None)) |
            (McpLlmAxisScore.server_id == "") |
            (McpLlmAxisScore.escalated == True) |
            (McpLlmAxisScore.probs.is_(None))
        )
    ).scalars().all()
    anomaly_count = len(anomaly_result)

    anomalies = [
        {"id": a.id, "server_id": a.server_id, "escalated": a.escalated, "reason": _detect_anomaly_reason(a)}
        for a in anomaly_result
    ]

    last_run_at = session.execute(
        select(func.max(McpLlmAxisScore.scored_at))
    ).scalar()

    if total_runs == 0:
        health_status = "unhealthy"
    elif recent_runs == 0:
        health_status = "degraded"
    elif anomaly_count > 0:
        health_status = "degraded"
    else:
        health_status = "healthy"

    return {
        "total_runs": total_runs,
        "recent_runs": recent_runs,
        "anomaly_count": anomaly_count,
        "health_status": health_status,
        "anomalies": anomalies,
        "last_run_at": last_run_at
    }


def _detect_anomaly_reason(score: McpLlmAxisScore) -> str:
    if score.server_id is None or score.server_id == "":
        return "missing_server_id"
    if score.escalated:
        return "escalated"
    if score.probs is None:
        return "missing_probs"
    return "unknown"


@router.get("/scoring/runs/health", response_model=HealthResponse)
async def get_health(
    session: Session = Depends(get_session)
) -> HealthResponse:
    health_data = check_scoring_run_health(session)
    return HealthResponse(status="ok", data=ScoringRunHealth(**health_data))


def create_app() -> FastAPI:
    app = FastAPI(title="Scoring Run Health Monitor")
    app.include_router(router)
    return app


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    db = TestingSessionLocal()
    try:
        now = datetime.now(timezone.utc)
        run1 = McpLlmAxisScore(
            adapter_sha256="sha_abc123",
            axis_name="safety",
            decision_rule_version="v1",
            escalated=False,
            id="run_1",
            label="safe",
            label_index=0,
            model_version="gpt-4",
            p_critical=0.05,
            p_danger=0.10,
            p_top=0.85,
            probs="[0.05, 0.10, 0.85]",
            scored_at=now - timedelta(minutes=10),
            server_id="server_abc"
        )
        run2 = McpLlmAxisScore(
            adapter_sha256="sha_def456",
            axis_name="safety",
            decision_rule_version="v1",
            escalated=False,
            id="run_2",
            label="risky",
            label_index=1,
            model_version="gpt-4",
            p_critical=0.30,
            p_danger=0.40,
            p_top=0.30,
            probs="[0.30, 0.40, 0.30]",
            scored_at=now - timedelta(minutes=30),
            server_id="server_def"
        )
        run3 = McpLlmAxisScore(
            adapter_sha256="sha_ghi789",
            axis_name="safety",
            decision_rule_version="v1",
            escalated=False,
            id="run_3",
            label="neutral",
            label_index=2,
            model_version="gpt-4",
            p_critical=0.15,
            p_danger=0.25,
            p_top=0.60,
            probs="[0.15, 0.25, 0.60]",
            scored_at=now - timedelta(minutes=50),
            server_id="server_ghi"
        )
        db.add(run1)
        db.add(run2)
        db.add(run3)
        db.commit()
    finally:
        db.close()

    response = client.get("/api/scoring/runs/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["data"]["health_status"] == "healthy"
    assert data["data"]["total_runs"] == 3
    assert data["data"]["recent_runs"] == 3
    assert data["data"]["anomaly_count"] == 0
    assert data["data"]["last_run_at"] is not None

    print("PASS")