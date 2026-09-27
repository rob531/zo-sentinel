# deps: fastapi, pydantic, sqlalchemy
"""Score Staleness Summarizer – returns server staleness summary as a count + pct breakdown.

GET /api/scoring/staleness-summary
  Returns total server count and per-bucket counts with percentages.
  Buckets: <1h, 1-6h, 6-24h, 1-7d, >7d, never_scored.

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on mcp_llm_axis_scores / mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_staleness_summarizer"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class BucketResponse(BaseModel):
    label: str = Field(..., description="Staleness bucket label")
    count: int = Field(..., description="Number of servers in this bucket")
    pct: float = Field(..., description="Percentage of total servers in this bucket")


class StalenessSummaryResponse(BaseModel):
    servers_total: int = Field(..., description="Total number of registered servers")
    buckets: List[BucketResponse] = Field(
        ...,
        description="Staleness buckets in order",
    )


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

def _bucket_label(age: timedelta) -> str:
    if age < timedelta(hours=1):
        return "<1h"
    if age < timedelta(hours=6):
        return "1-6h"
    if age < timedelta(hours=24):
        return "6-24h"
    if age < timedelta(days=7):
        return "1-7d"
    return ">7d"


def staleness_summary_logic(db: Session) -> StalenessSummaryResponse:
    # Total registered servers
    total = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one()

    # Subquery: most recent scored_at per server
    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Fetch server_id + latest_at for all servers with at least one score
    scored_rows = db.execute(
        select(subq.c.server_id, subq.c.latest_at)
    ).fetchall()

    # Map server_id -> latest_at
    latest_by_server = {row[0]: row[1] for row in scored_rows}

    # Bucket counts
    now = datetime.utcnow()
    bucket_counts = {"<1h": 0, "1-6h": 0, "6-24h": 0, "1-7d": 0, ">7d": 0}
    for server_id, latest_at in latest_by_server.items():
        age = now - latest_at
        bucket = _bucket_label(age)
        bucket_counts[bucket] += 1

    never_scored_count = total - len(latest_by_server)

    # Ordered result
    bucket_order = ["<1h", "1-6h", "6-24h", "1-7d", ">7d", "never_scored"]
    buckets = []
    for label in bucket_order:
        count = bucket_counts.get(label, 0) if label != "never_scored" else never_scored_count
        pct = round((count / total * 100), 2) if total else 0.0
        buckets.append(BucketResponse(label=label, count=count, pct=pct))

    return StalenessSummaryResponse(servers_total=total, buckets=buckets)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/staleness-summary",
    response_model=StalenessSummaryResponse,
    summary="Get score staleness summary",
)
def staleness_summary(
    db: Session = Depends(get_session),
) -> StalenessSummaryResponse:
    """
    Return a staleness summary for all registered servers based on their
    most recent LLM axis score timestamp. Returns bucket counts and
    percentages for each staleness bucket.
    """
    return staleness_summary_logic(db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from collections import Counter
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override_get_session() -> Generator[Session, None, None]:
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()

    # Seed: 10 servers total
    # srv0-4: recent scores <1h
    # srv5-6: scores in 1-6h / 6-24h
    # srv7: score >7d
    # srv8-9: never scored
    with TestSessionLocal() as sess:
        for i in range(5):
            sess.add(McpServerRegistry(
                server_id=f"srv{i}", name=f"Server {i}",
                registry_source="self-test", url=f"http://srv{i}.test",
            ))
            sess.add(McpLlmAxisScore(
                server_id=f"srv{i}", axis_name="test",
                scored_at=now - timedelta(minutes=30), label="low",
            ))

        sess.add(McpServerRegistry(
            server_id="srv5", name="Server 5",
            registry_source="self-test", url="http://srv5.test",
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv5", axis_name="test",
            scored_at=now - timedelta(hours=2), label="low",
        ))

        sess.add(McpServerRegistry(
            server_id="srv6", name="Server 6",
            registry_source="self-test", url="http://srv6.test",
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv6", axis_name="test",
            scored_at=now - timedelta(hours=12), label="medium",
        ))

        sess.add(McpServerRegistry(
            server_id="srv7", name="Server 7",
            registry_source="self-test", url="http://srv7.test",
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv7", axis_name="test",
            scored_at=now - timedelta(days=10), label="high",
        ))

        # never scored
        sess.add(McpServerRegistry(
            server_id="srv8", name="Server 8",
            registry_source="self-test", url="http://srv8.test",
        ))
        sess.add(McpServerRegistry(
            server_id="srv9", name="Server 9",
            registry_source="self-test", url="http://srv9.test",
        ))
        sess.commit()

    client = TestClient(test_app)

    resp = client.get("/api/scoring/staleness-summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    assert data["servers_total"] == 10, f"servers_total: expected 10, got {data['servers_total']}"

    bucket_labels = {b["label"] for b in data["buckets"]}
    expected_labels = {"<1h", "1-6h", "6-24h", "1-7d", ">7d", "never_scored"}
    assert expected_labels.issubset(bucket_labels), f"Missing buckets: {expected_labels - bucket_labels}"

    bucket_map = {b["label"]: b for b in data["buckets"]}
    assert bucket_map["<1h"]["count"] == 5, f"<1h: expected 5, got {bucket_map['<1h']['count']}"
    assert bucket_map["1-6h"]["count"] == 1, f"1-6h: expected 1, got {bucket_map['1-6h']['count']}"
    assert bucket_map["6-24h"]["count"] == 1, f"6-24h: expected 1, got {bucket_map['6-24h']['count']}"
    assert bucket_map["1-7d"]["count"] == 0, f"1-7d: expected 0, got {bucket_map['1-7d']['count']}"
    assert bucket_map[">7d"]["count"] == 1, f">7d: expected 1, got {bucket_map['>7d']['count']}"
    assert bucket_map["never_scored"]["count"] == 2, f"never_scored: expected 2, got {bucket_map['never_scored']['count']}"

    # Check pct sums to ~100
    total_pct = sum(b["pct"] for b in data["buckets"])
    assert 99 <= total_pct <= 101, f"pct sum should be ~100, got {total_pct}"

    print("PASS")
