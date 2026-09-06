# deps: fastapi, sqlalchemy, pydantic
"""score_axis_drift_detector -- detect score drift on a single axis over time.

GET /api/scoring/axis-drift
  Query params: server_id, axis_name, hours (default 24)
  Returns drift_delta (max adjacent p_top delta), drift_label (label changed),
  run_count, and the sample list ordered by scored_at desc.

Auth: public.
Data: app tier via get_session + McpLlmAxisScore.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["score_axis_drift_detector"])


class AxisDriftSample(BaseModel):
    scored_at: datetime
    p_top: float
    label: str | None


class AxisDriftResponse(BaseModel):
    server_id: str
    axis_name: str
    drift_delta: float
    drift_label: bool
    run_count: int
    samples: list[AxisDriftSample]


@router.get("/scoring/axis-drift", response_model=AxisDriftResponse)
def get_axis_drift(
    server_id: str = Query(..., description="Server identifier"),
    axis_name: str = Query(..., description="Axis name to analyse"),
    hours: int = Query(24, description="Look-back window in hours"),
    session: Session = Depends(get_session),
) -> AxisDriftResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    scores = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for this server/axis window")

    sorted_scores = sorted(scores, key=lambda s: s.scored_at)

    drift_delta = 0.0
    drift_label = False
    if len(sorted_scores) >= 2:
        for i in range(1, len(sorted_scores)):
            p_curr = float(sorted_scores[i].p_top or 0)
            p_prev = float(sorted_scores[i - 1].p_top or 0)
            drift_delta = max(drift_delta, abs(p_curr - p_prev))
            if sorted_scores[i].label != sorted_scores[i - 1].label:
                drift_label = True

    samples = [
        AxisDriftSample(
            scored_at=s.scored_at,
            p_top=float(s.p_top) if s.p_top is not None else 0.0,
            label=s.label,
        )
        for s in sorted_scores
    ]

    return AxisDriftResponse(
        server_id=server_id,
        axis_name=axis_name,
        drift_delta=drift_delta,
        drift_label=drift_label,
        run_count=len(samples),
        samples=samples,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from typing import Generator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = get_test_session

    # Seed data: descending timestamps, label flips at index 1→2
    now = datetime.now(timezone.utc)
    records = [
        McpLlmAxisScore(
            id=1,
            server_id="drift-srv-001",
            axis_name="safety",
            p_top=0.90,
            label="A",
            scored_at=now - timedelta(hours=4),
            adapter_sha256="sha256-a",
            model_version="v1",
            decision_rule_version="r1",
        ),
        McpLlmAxisScore(
            id=2,
            server_id="drift-srv-001",
            axis_name="safety",
            p_top=0.60,
            label="B",
            scored_at=now - timedelta(hours=2),
            adapter_sha256="sha256-a",
            model_version="v1",
            decision_rule_version="r1",
        ),
        McpLlmAxisScore(
            id=3,
            server_id="drift-srv-001",
            axis_name="safety",
            p_top=0.30,
            label="C",
            scored_at=now,
            adapter_sha256="sha256-a",
            model_version="v1",
            decision_rule_version="r1",
        ),
    ]

    session = TestingSessionLocal()
    for r in records:
        session.add(r)
    session.commit()
    session.close()

    client = TestClient(that_app)

    resp = client.get(
        "/api/scoring/axis-drift",
        params={"server_id": "drift-srv-001", "axis_name": "safety", "hours": 72},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    # sorted ascending: 0.30→0.60 (delta 0.30), 0.60→0.90 (delta 0.30) → max 0.30
    assert abs(data["drift_delta"] - 0.30) < 1e-9, f"Expected drift_delta=0.30, got {data['drift_delta']}"
    # label flips at index 1→2 (B→C)
    assert data["drift_label"] is True, f"Expected drift_label=True, got {data['drift_label']}"
    assert data["run_count"] == 3, f"Expected run_count=3, got {data['run_count']}"
    assert len(data["samples"]) == 3, f"Expected 3 samples, got {len(data['samples'])}"

    # Test 404 for unknown server/axis
    resp404 = client.get(
        "/api/scoring/axis-drift",
        params={"server_id": "nonexistent", "axis_name": "unknown", "hours": 24},
    )
    assert resp404.status_code == 404

    print("PASS")
