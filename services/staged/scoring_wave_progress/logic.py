# services/staged/scoring_wave_progress/logic.py
from datetime import datetime, timedelta
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class WaveProgressResponse(BaseModel):
    total_servers: int
    never_scanned: int
    pending_score: int
    scored: int
    by_risk_tier: Dict[str, int]
    wave_count_7d: int


@router.get("/scoring/wave-progress", response_model=WaveProgressResponse)
def get_wave_progress(session: Session = Depends(get_session)):
    # total servers
    total = session.query(func.count(McpServerRegistry.server_id)).scalar()

    # never scanned
    never_scanned = (
        session.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.last_scanned.is_(None))
        .scalar()
    )

    # scored servers (at least one score)
    scored_subq = (
        session.query(McpLlmAxisScore.server_id).distinct().subquery()
    )
    scored = (
        session.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.server_id.in_(scored_subq))
        .scalar()
    )

    # pending score = total - never_scanned - scored
    pending_score = total - never_scanned - scored

    # breakdown by risk tier for scored servers
    tier_rows = (
        session.query(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_risk_tier = {tier: count for tier, count in tier_rows}

    # rolling 7‑day wave count from cadence_job_runs
    cutoff = datetime.utcnow() - timedelta(days=7)
    wave_count_7d = (
        session.query(func.count(CadenceJobRun.id))
        .filter(CadenceJobRun.started_at >= cutoff)
        .scalar()
    )

    return WaveProgressResponse(
        total_servers=total,
        never_scanned=never_scanned,
        pending_score=pending_score,
        scored=scored,
        by_risk_tier=by_risk_tier,
        wave_count_7d=wave_count_7d,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Override dependency
    async def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with SessionLocal() as db:
        # 5 never‑scanned servers
        for i in range(5):
            db.add(
                McpServerRegistry(
                    server_id=f"ns-{i}",
                    name=f"NeverScanned{i}",
                    last_scanned=None,
                    risk_tier="low",
                )
            )
        # 5 scored servers across two tiers
        for i in range(5):
            server_id = f"sc-{i}"
            tier = "high" if i % 2 == 0 else "medium"
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Scored{i}",
                    last_scanned=datetime.utcnow(),
                    risk_tier=tier,
                )
            )
            db.add(
                McpLlmAxisScore(
                    id=i,
                    server_id=server_id,
                    axis_name="test_axis",
                    label="test_label",
                    label_index=0,
                    model_version="v1",
                    p_critical=0.0,
                    p_danger=0.0,
                    p_top=0.0,
                    probs={},
                    scored_at=datetime.utcnow(),
                )
            )
        # 2 cadence job runs within last 7 days
        now = datetime.utcnow()
        for i in range(2):
            db.add(
                CadenceJobRun(
                    id=i,
                    job="wave",
                    detail="test",
                    status="completed",
                    started_at=now - timedelta(days=1),
                    finished_at=now,
                    rows_affected=10,
                )
            )
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/scoring/wave-progress")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["never_scanned"] == 5, f"never_scanned={data['never_scanned']}"
    assert data["scored"] == 5, f"scored={data['scored']}"
    print("PASS")