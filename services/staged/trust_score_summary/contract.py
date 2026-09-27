"""services/staged/trust_score_summary/contract.py

FastAPI contract for the trust score summary endpoint.
Mirrors the exemplar contract implementation.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class Bucket(BaseModel):
    range: str
    count: int
    pct: float


class Summary(BaseModel):
    buckets: List[Bucket]
    total: int
    mean: Optional[float] = None
    median: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


_BUCKET_RANGES = [
    (0, 20),
    (20, 40),
    (40, 60),
    (60, 80),
    (80, 100),
]


def _compute_statistics(scores: List[float]) -> dict:
    if not scores:
        return {"mean": None, "median": None, "min": None, "max": None}
    sorted_scores = sorted(scores)
    n = len(sorted_scores)
    mean = sum(sorted_scores) / n
    median = (
        (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2
        if n % 2 == 0
        else sorted_scores[n // 2]
    )
    return {
        "mean": mean,
        "median": median,
        "min": sorted_scores[0],
        "max": sorted_scores[-1],
    }


@router.get("/trust/summary", response_model=Summary)
def get_trust_score_summary(db: Session = Depends(get_session)) -> Summary:
    servers = db.execute(select(McpServerRegistry)).scalars().all()
    total = len(servers)

    # Extract non‑null trust scores
    scores = [s.trust_score for s in servers if s.trust_score is not None]

    # Bucket counts
    bucket_data: List[Bucket] = []
    for low, high in _BUCKET_RANGES:
        count = sum(
            1
            for s in servers
            if s.trust_score is not None and low <= s.trust_score < high
            or (high == 100 and s.trust_score == 100)
        )
        pct = (count / total * 100) if total else 0.0
        bucket_data.append(
            Bucket(range=f"{low}-{high}", count=count, pct=round(pct, 2))
        )

    stats = _compute_statistics(scores)

    return Summary(
        buckets=bucket_data,
        total=total,
        mean=stats["mean"],
        median=stats["median"],
        min=stats["min"],
        max=stats["max"],
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.trust_score_summary.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # Build a temporary FastAPI app with an in‑memory SQLite DB
    test_app = FastAPI()
    test_app.include_router(router)

    # SQLite in‑memory engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables for the models we use
    McpServerRegistry.__table__.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSession() as sess:
        sess.add_all(
            [
                McpServerRegistry(server_id="s1", trust_score=10),
                McpServerRegistry(server_id="s2", trust_score=30),
                McpServerRegistry(server_id="s3", trust_score=50),
                McpServerRegistry(server_id="s4", trust_score=70),
                McpServerRegistry(server_id="s5", trust_score=90),
            ]
        )
        sess.commit()

    client = TestClient(test_app)
    resp = client.get("/api/trust/summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict), "Response is not a dict"
    assert len(data.get("buckets", [])) == 5, "Expected 5 buckets"
    assert data.get("total") == 5, "Expected total count of 5"
    assert isinstance(data.get("mean"), (float, int)), "Mean not computed"
    print("PASS")
    sys.exit(0)