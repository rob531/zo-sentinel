from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry


class ScoringPipelineHealthResponse(BaseModel):
    scored_today: int
    scored_this_week: int
    pending_finalize_count: int
    score_age_hours_p50: float | None
    score_age_hours_p95: float | None
    pipeline_status: str


def get_scoring_pipeline_health(db: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    age_stats = db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE scored_at >= :today_start) AS scored_today,
                COUNT(*) FILTER (WHERE scored_at >= :week_start) AS scored_this_week,
                COUNT(*) FILTER (WHERE label IS NULL) AS pending_finalize,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (:now - scored_at)) / 3600)::float AS p50,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (:now - scored_at)) / 3600)::float AS p95
            FROM mcp_llm_axis_scores
        """),
        {"today_start": today_start, "week_start": week_start, "now": now}
    ).fetchone()

    scored_today = age_stats.scored_today or 0
    scored_this_week = age_stats.scored_this_week or 0
    pending_finalize_count = age_stats.pending_finalize or 0
    score_age_hours_p50 = age_stats.p50
    score_age_hours_p95 = age_stats.p95

    pipeline_status = "HEALTHY"
    if score_age_hours_p95 is not None and score_age_hours_p95 > 24:
        pipeline_status = "STALLED"
    if pending_finalize_count > 500:
        pipeline_status = "STALLED"

    return {
        "scored_today": scored_today,
        "scored_this_week": scored_this_week,
        "pending_finalize_count": pending_finalize_count,
        "score_age_hours_p50": round(score_age_hours_p50, 2) if score_age_hours_p50 is not None else None,
        "score_age_hours_p95": round(score_age_hours_p95, 2) if score_age_hours_p95 is not None else None,
        "pipeline_status": pipeline_status,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    McpLlmAxisScore.metadata.create_all(engine)
    CadenceJobRun.metadata.create_all(engine)
    McpServerRegistry.metadata.create_all(engine)

    session = SessionLocal()
    now = datetime.utcnow()

    session.add(McpLlmAxisScore(server_id="srv1", axis_name="risk", scored_at=now, label="DANGER"))
    session.add(McpLlmAxisScore(server_id="srv2", axis_name="risk", scored_at=now - timedelta(hours=2), label="SAFE"))
    session.add(McpLlmAxisScore(server_id="srv3", axis_name="risk", scored_at=now - timedelta(hours=25)))

    session.add(CadenceJobRun(
        job="scoring", status="completed", rows_affected=10,
        started_at=now - timedelta(hours=1), finished_at=now
    ))
    session.add(CadenceJobRun(
        job="scoring_finalize", status="completed", rows_affected=5,
        started_at=now - timedelta(minutes=30), finished_at=now
    ))

    session.add(McpServerRegistry(
        server_id="srv1", name="test-server", last_seen=now - timedelta(minutes=5),
        last_scanned=now - timedelta(minutes=10)
    ))

    session.commit()

    app = FastAPI()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/scoring/pipeline-health")
    def endpoint():
        return get_scoring_pipeline_health(next(override_get_session()))

    import uvicorn
    import threading
    import time
    import urllib.request

    server = uvicorn.Server(config=uvicorn.Config(app, host="127.0.0.1", port=18771, log_level="error"))
    thread = threading.Thread(target=server.run)
    thread.daemon = True
    thread.start()
    time.sleep(0.5)

    try:
        response = urllib.request.urlopen("http://127.0.0.1:18771/api/scoring/pipeline-health", timeout=5)
        assert response.status == 200, f"Expected 200, got {response.status}"
        data = eval(response.read().decode())
        assert data["pipeline_status"] == "HEALTHY", f"Expected HEALTHY, got {data['pipeline_status']}"
        print("PASS")
    finally:
        server.should_exit = True