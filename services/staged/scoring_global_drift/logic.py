# services/staged/scoring_global_drift/logic.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_global_drift"])


class MetricItem(BaseModel):
    metric: str = Field(..., description="Name of the metric (e.g., axis name)")
    value: float = Field(..., description="Computed value for the metric")


class GlobalDriftResponse(BaseModel):
    global_drift: float = Field(..., description="Overall drift across all axes")
    metrics: List[MetricItem] = Field(..., description="Per‑axis drift metrics")


@router.get(
    "/scoring/drift",
    response_model=GlobalDriftResponse,
    summary="Compute global drift metrics across all servers",
)
def get_global_drift(session: Session = Depends(get_session)):
    """
    Reads all rows from ``mcp_llm_axis_scores`` and computes:
    * **global_drift** – the average of ``p_critical`` over the whole table.
    * **metrics** – per‑axis average of ``p_critical``.
    """
    # Ensure there is data
    total_count = session.query(func.count(McpLlmAxisScore.id)).scalar()
    if total_count == 0:
        raise HTTPException(status_code=404, detail="No axis scores found")

    # Global average of p_critical
    global_avg = (
        session.query(func.avg(McpLlmAxisScore.p_critical)).scalar() or 0.0
    )

    # Per‑axis averages
    per_axis = (
        session.query(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_critical).label("axis_avg"),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )

    metrics = [
        MetricItem(metric=axis_name, value=float(axis_avg))
        for axis_name, axis_avg in per_axis
    ]

    return GlobalDriftResponse(global_drift=float(global_avg), metrics=metrics)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Populate with deterministic test data (10 servers, same axis)
    def seed_data():
        sess = TestingSessionLocal()
        rows = []
        for i in range(10):
            rows.append(
                McpLlmAxisScore(
                    id=i + 1,
                    adapter_sha256=f"dummy_sha_{i}",
                    axis_name="test_axis",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    label="label",
                    label_index=0,
                    model_version="model_1",
                    p_critical=i * 0.1,  # 0.0, 0.1, … 0.9
                    p_danger=0.0,
                    p_top=0.0,
                    probs="{}",
                    scored_at=datetime.datetime.utcnow(),
                    server_id=i,
                )
            )
        sess.bulk_save_objects(rows)
        sess.commit()
        sess.close()

    seed_data()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    response = client.get("/api/scoring/drift")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()

    # Expected global average of p_critical = (0+0.1+…+0.9)/10 = 0.45
    expected_avg = 0.45
    assert abs(data["global_drift"] - expected_avg) < 1e-6, "global_drift mismatch"
    assert isinstance(data["metrics"], list), "metrics not a list"
    assert len(data["metrics"]) == 1, "unexpected number of metrics"
    metric = data["metrics"][0]
    assert metric["metric"] == "test_axis", "metric name mismatch"
    assert abs(metric["value"] - expected_avg) < 1e-6, "metric value mismatch"

    print("PASS")