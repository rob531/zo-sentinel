# deps: fastapi, pydantic, sqlalchemy
"""Dashboard API for signal scores distribution.

Provides a GET endpoint that returns the distribution of LLM axis scores
across risk buckets for dashboard visualization.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class BucketCount(BaseModel):
    bucket: str = Field(..., description="Risk bucket name")
    count: int = Field(..., description="Number of servers in bucket")
    percentage: float = Field(..., description="Percentage of total servers")


class AxisDistribution(BaseModel):
    axis_name: str = Field(..., description="Name of the axis")
    buckets: List[BucketCount] = Field(..., description="Count per bucket")
    total_servers: int = Field(..., description="Total servers scored")


class SignalScoresDistributionResponse(BaseModel):
    distribution: List[AxisDistribution] = Field(
        ..., description="Distribution per axis"
    )
    days: int = Field(..., description="Lookback window in days")
    generated_at: str = Field(..., description="ISO timestamp of generation")


# ----------------------------------------------------------------------
# Bucket definitions (based on p_top probability ranges)
# ----------------------------------------------------------------------
_BUCKETS = [
    ("CRITICAL", 0.0, 0.2),
    ("HIGH", 0.2, 0.4),
    ("MEDIUM", 0.4, 0.6),
    ("LOW", 0.6, 0.8),
    ("NEGLIGIBLE", 0.8, 1.01),
]


def _get_bucket(p_top: Optional[float]) -> str:
    """Return bucket name for a p_top probability value."""
    if p_top is None:
        return "UNKNOWN"
    for name, lo, hi in _BUCKETS:
        if lo <= p_top < hi:
            return name
    return "UNKNOWN"


# ----------------------------------------------------------------------
# FastAPI router
# ----------------------------------------------------------------------
router = APIRouter(
    prefix="/api",
    tags=["dashboard_api_for_signal_scores_distribution"],
)


@router.get(
    "/signal_scores/distribution",
    response_model=SignalScoresDistributionResponse,
    summary="Get signal scores distribution by axis and risk bucket",
)
def get_signal_scores_distribution(
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Number of days to look back",
    ),
    axis: Optional[str] = Query(
        default=None,
        description="Filter to a specific axis (e.g., 'overall_risk')",
    ),
    session: Session = Depends(get_session),
) -> SignalScoresDistributionResponse:
    """
    Return the distribution of LLM axis scores bucketed by risk level.

    Buckets are defined by p_top (probability of the top/most likely label):
    - CRITICAL: p_top in [0.0, 0.2)
    - HIGH: p_top in [0.2, 0.4)
    - MEDIUM: p_top in [0.4, 0.6)
    - LOW: p_top in [0.6, 0.8)
    - NEGLIGIBLE: p_top in [0.8, 1.0]
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Build base query for scores within the time window
    query = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.scored_at >= cutoff
    )

    # Optionally filter by axis
    if axis:
        query = query.filter(McpLlmAxisScore.axis_name == axis)

    scores = query.all()

    if not scores:
        raise HTTPException(
            status_code=404,
            detail="No scores found for the given period and filters",
        )

    # Get unique servers that have scores
    server_ids = {s.server_id for s in scores}
    total_servers = len(server_ids)

    # Group scores by axis
    axis_scores: dict[str, List[Optional[float]]] = {}
    for score in scores:
        if score.axis_name not in axis_scores:
            axis_scores[score.axis_name] = []
        axis_scores[score.axis_name].append(score.p_top)

    # Build distribution per axis
    distribution: List[AxisDistribution] = []
    for axis_name, p_values in axis_scores.items():
        bucket_counts: dict[str, int] = {b[0]: 0 for b in _BUCKETS}
        bucket_counts["UNKNOWN"] = 0

        for p in p_values:
            bucket = _get_bucket(p)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        buckets = [
            BucketCount(bucket=name, count=cnt, percentage=round(cnt / len(p_values) * 100, 2) if p_values else 0.0)
            for name, cnt in bucket_counts.items()
            if name != "UNKNOWN"
        ]
        # Sort buckets by severity order
        severity_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NEGLIGIBLE"]
        buckets.sort(key=lambda x: severity_order.index(x.bucket) if x.bucket in severity_order else 99)

        distribution.append(
            AxisDistribution(
                axis_name=axis_name,
                buckets=buckets,
                total_servers=total_servers,
            )
        )

    # Sort axes alphabetically
    distribution.sort(key=lambda x: x.axis_name)

    return SignalScoresDistributionResponse(
        distribution=distribution,
        days=days,
        generated_at=datetime.utcnow().isoformat(),
    )


# ----------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # Build in-memory SQLite test DB
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    now = datetime.utcnow()
    with SessionLocal() as db:
        # Add servers
        servers = [
            McpServerRegistry(server_id=f"test_server_{i}", name=f"Server {i}")
            for i in range(10)
        ]
        db.add_all(servers)
        db.flush()

        # Add scores across different axes and p_top values
        axes = ["overall_risk", "auth_strength", "capability_breadth"]
        for i, axis in enumerate(axes):
            for j, server in enumerate(servers):
                # Spread p_top values across buckets
                p_top = (i * 10 + j * 2) / 100.0
                score = McpLlmAxisScore(
                    server_id=server.server_id,
                    axis_name=axis,
                    p_top=p_top,
                    label="test",
                    label_index=0,
                    model_version="test-v1",
                    scored_at=now - timedelta(days=j % 5),
                )
                db.add(score)
        db.commit()

    # Override dependency
    def _override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # Test happy path
    resp = client.get("/api/signal_scores/distribution?days=30")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    assert "distribution" in data, "Missing 'distribution' key"
    assert "days" in data, "Missing 'days' key"
    assert "generated_at" in data, "Missing 'generated_at' key"
    assert len(data["distribution"]) == 3, f"Expected 3 axes, got {len(data['distribution'])}"

    # Verify structure of each axis
    for axis_data in data["distribution"]:
        assert "axis_name" in axis_data
        assert "buckets" in axis_data
        assert "total_servers" in axis_data
        # Verify we have the expected bucket names
        bucket_names = [b["bucket"] for b in axis_data["buckets"]]
        assert "CRITICAL" in bucket_names
        assert "HIGH" in bucket_names
        assert "MEDIUM" in bucket_names
        assert "LOW" in bucket_names
        assert "NEGLIGIBLE" in bucket_names

    # Test axis filter
    resp = client.get("/api/signal_scores/distribution?days=30&axis=overall_risk")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["distribution"]) == 1
    assert data["distribution"][0]["axis_name"] == "overall_risk"

    # Test with no results (empty server list)
    with SessionLocal() as db:
        db.query(McpLlmAxisScore).delete()
        db.query(McpServerRegistry).delete()
        db.commit()

    resp = client.get("/api/signal_scores/distribution?days=1")
    assert resp.status_code == 404, f"Expected 404 for empty data, got {resp.status_code}"

    print("PASS")
    sys.exit(0)
