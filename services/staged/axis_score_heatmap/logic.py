"""Axis score heatmap service."""
from typing import Any

from app.db import get_session
from app.models import McpLlmAxisScore

from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api/axis", tags=["axis"])

RISK_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

BUCKET_RANGES = [
    (0, 20),
    (20, 40),
    (40, 60),
    (60, 80),
    (80, 100),
]


class BucketData(BaseModel):
    range: str
    counts: list[int]


class HeatmapResponse(BaseModel):
    axes: list[str]
    buckets: list[BucketData]
    total_servers: int


def get_bucket_index(p_top: float) -> int:
    for i, (low, high) in enumerate(BUCKET_RANGES):
        if low <= p_top < high:
            return i
    return len(BUCKET_RANGES) - 1


def compute_axis_heatmap(limit: int = 100) -> dict[str, Any]:
    """Compute axis score heatmap from mcp_llm_axis_scores."""
    with get_session() as session:
        scores = session.query(McpLlmAxisScore).limit(limit).all()

        axes = RISK_AXES
        buckets = []
        for low, high in BUCKET_RANGES:
            bucket_counts = []
            for axis in axes:
                count = sum(
                    1 for s in scores
                    if s.axis_name == axis and low <= (s.p_top or 0) < high
                )
                bucket_counts.append(count)
            buckets.append({"range": f"{low}-{high}", "counts": bucket_counts})

        total_servers = session.query(McpLlmAxisScore.server_id).distinct().count()

        return {"axes": axes, "buckets": buckets, "total_servers": total_servers}


@router.get("/heatmap", response_model=HeatmapResponse)
def get_heatmap(limit: int = 100):
    """Compute axis score heatmap from mcp_llm_axis_scores."""
    return compute_axis_heatmap(limit=limit)


if __name__ == "__main__":
    import os
    import tempfile

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base
    from app.main import app

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=engine
        )

        def get_test_session():
            session = TestingSessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_session] = get_test_session

        session = TestingSessionLocal()
        try:
            test_servers = [f"server_{i}" for i in range(10)]
            for i, server_id in enumerate(test_servers):
                for axis in RISK_AXES[:3]:
                    p_top = (i * 10) % 90
                    session.add(
                        McpLlmAxisScore(
                            server_id=server_id,
                            axis_name=axis,
                            p_top=p_top,
                        )
                    )
            session.commit()

            client = TestClient(app)
            response = client.get("/api/axis/heatmap?limit=100")
            assert response.status_code == 200

            data = response.json()
            assert len(data["axes"]) == 7
            assert len(data["buckets"]) == 5

            print("PASS")
        finally:
            session.close()