import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from app.db import get_session, Base
from app.models import McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")


class Bucket(BaseModel):
    bucket: str
    count: int
    share: float


class StaleSource(BaseModel):
    registry_source: str
    stale_count: int


class FreshnessResponse(BaseModel):
    days: int
    total_servers: int
    buckets: List[Bucket]
    stale_sources: List[StaleSource]
    generated_at: datetime.datetime = Field(..., alias="generated_at")


def _bucket_name(delta: Optional[datetime.timedelta]) -> str:
    if delta is None:
        return "never_assessed"
    hours = delta.total_seconds() / 3600
    if hours < 24:
        return "fresh"
    if 24 <= hours < 72:
        return "recent"
    if 72 <= hours < 168:
        return "stale"
    return "overdue"


@router.get(
    "/servers/freshness",
    response_model=FreshnessResponse,
    name="get_freshness",
)
def get_freshness(
    days: int = Query(7, ge=1),
    session: Session = Depends(get_session),
):
    now = datetime.datetime.utcnow()
    servers = session.query(McpServerRegistry).all()

    bucket_counts = {
        "fresh": 0,
        "recent": 0,
        "stale": 0,
        "overdue": 0,
        "never_assessed": 0,
    }
    stale_source_counts = {}

    for srv in servers:
        last = srv.last_assessed
        delta = None if last is None else now - last
        bucket = _bucket_name(delta)
        bucket_counts[bucket] += 1

        if bucket in ("stale", "overdue"):
            src = srv.registry_source or "unknown"
            stale_source_counts[src] = stale_source_counts.get(src, 0) + 1

    total = len(servers) or 1  # avoid division by zero
    buckets = [
        Bucket(bucket=b, count=c, share=round(c / total, 4))
        for b, c in bucket_counts.items()
    ]

    stale_sources = [
        StaleSource(registry_source=src, stale_count=count)
        for src, count in stale_source_counts.items()
    ]

    return FreshnessResponse(
        days=days,
        total_servers=len(servers),
        buckets=buckets,
        stale_sources=stale_sources,
        generated_at=now,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build a test FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.include_router(router)

    # SQLite in‑memory engine
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Dependency override
    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    with TestSession() as db:
        servers = [
            McpServerRegistry(
                server_id="srv1",
                name="Server One",
                registry_source="srcA",
                risk_tier="high",
                verdict="clean",
                trust_score=1.0,
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="srv2",
                name="Server Two",
                registry_source="srcA",
                risk_tier="medium",
                verdict="clean",
                trust_score=0.9,
                last_assessed=now - datetime.timedelta(days=2),
            ),
            McpServerRegistry(
                server_id="srv3",
                name="Server Three",
                registry_source="srcB",
                risk_tier="low",
                verdict="clean",
                trust_score=0.8,
                last_assessed=now - datetime.timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="srv4",
                name="Server Four",
                registry_source="srcC",
                risk_tier="low",
                verdict="clean",
                trust_score=0.7,
                last_assessed=None,
            ),
            McpServerRegistry(
                server_id="srv5",
                name="Server Five",
                registry_source="srcD",
                risk_tier="low",
                verdict="clean",
                trust_score=0.6,
                last_assessed=now - datetime.timedelta(days=10),
            ),
        ]
        db.add_all(servers)
        db.commit()

    # ------------------------------------------------------------------- #
    # Run test client
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    resp = client.get("/api/servers/freshness?days=7")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # total servers
    assert data["total_servers"] == 5, "total_servers mismatch"

    # bucket never_assessed count
    never_bucket = next(b for b in data["buckets"] if b["bucket"] == "never_assessed")
    assert never_bucket["count"] == 1, "never_assessed count mismatch"

    # stale sources count
    assert len(data["stale_sources"]) == 2, "stale_sources length mismatch"

    print("PASS")