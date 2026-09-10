from __future__ import annotations

import datetime
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry

router = scoring_router = FastAPI()


class PipelineHealthResponse(BaseModel):
    scored_today: int
    scored_this_week: int
    pending_finalize_count: int
    score_age_hours_p50: float
    score_age_hours_p95: float
    pipeline_status: str


@router.get("/api/scoring/pipeline-health", response_model=PipelineHealthResponse)
def get_pipeline_health(db: Session = Depends(get_session)) -> PipelineHealthResponse:
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - datetime.timedelta(days=7)
    day_ago = now - datetime.timedelta(hours=24)

    scored_today = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at >= today_start)
        .count()
    )

    scored_this_week = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at >= week_start)
        .count()
    )

    pending_finalize_count = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at >= day_ago, McpLlmAxisScore.escalated == False)  # noqa: E712
        .count()
    )

    p50: float = 0.0
    p95: float = 0.0
    score_row = (
        db.query(
            func.percentile_cont(0.5).within_group(
                (func.extract('epoch', now - McpLlmAxisScore.scored_at) / 3600.0)
            ).label('p50'),
            func.percentile_cont(0.95).within_group(
                (func.extract('epoch', now - McpLlmAxisScore.scored_at) / 3600.0)
            ).label('p95'),
        )
        .filter(McpLlmAxisScore.scored_at >= week_start)
        .first()
    )
    if score_row:
        p50 = float(score_row.p50) if score_row.p50 else 0.0
        p95 = float(score_row.p95) if score_row.p95 else 0.0

    pipeline_status = "STALLED" if (p95 > 24.0 or pending_finalize_count > 500) else "HEALTHY"

    return PipelineHealthResponse(
        scored_today=scored_today,
        scored_this_week=scored_this_week,
        pending_finalize_count=pending_finalize_count,
        score_age_hours_p50=round(p50, 2),
        score_age_hours_p95=round(p95, 2),
        pipeline_status=pipeline_status,
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        db = TestingSessionLocal()

        s1 = McpLlmAxisScore(
            id="ax-001",
            server_id="srv-001",
            scored_at=datetime.datetime.now() - datetime.timedelta(days=7),
            axis_name="risk",
            model_version="v1",
            escalated=False,
        )
        s2 = McpLlmAxisScore(
            id="ax-002",
            server_id="srv-001",
            scored_at=datetime.datetime.now() - datetime.timedelta(days=5),
            axis_name="risk",
            model_version="v1",
            escalated=False,
        )
        s3 = McpLlmAxisScore(
            id="ax-003",
            server_id="srv-002",
            scored_at=datetime.datetime.now() - datetime.timedelta(hours=1),
            axis_name="risk",
            model_version="v1",
            escalated=True,
        )
        db.add_all([s1, s2, s3])

        f1 = CadenceJobRun(
            id="fin-001",
            job="scoring_finalize",
            status="completed",
            rows_affected=10,
            started_at=datetime.datetime.now() - datetime.timedelta(hours=2),
            finished_at=datetime.datetime.now() - datetime.timedelta(hours=1),
        )
        f2 = CadenceJobRun(
            id="fin-002",
            job="scoring_finalize",
            status="completed",
            rows_affected=5,
            started_at=datetime.datetime.now() - datetime.timedelta(hours=26),
            finished_at=datetime.datetime.now() - datetime.timedelta(hours=25),
        )
        db.add_all([f1, f2])
        db.commit()
        db.close()

        response = client.get("/api/scoring/pipeline-health")
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_status"] == "HEALTHY"
        print("PASS")