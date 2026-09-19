# deps: fastapi, pydantic, sqlalchemy
"""Score Staleness Probe – reports how stale each server's LLM axis scores are.

GET /api/scoring/staleness-report
  Returns per-bucket counts and up to 5 example server IDs per bucket.
  Buckets: <1h, 1-6h, 6-24h, 1-7d, 7-30d, >30d (unscored + >30d scored).

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on mcp_llm_axis_scores / mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_staleness_probe"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class StalenessBucket(BaseModel):
    bucket: str = Field(..., description="Time bucket label")
    count: int = Field(..., description="Number of servers in this bucket")
    examples: List[str] = Field(
        default_factory=list,
        description="Up to 5 server IDs as examples",
    )


class StalenessReportResponse(BaseModel):
    generated_at: datetime = Field(..., description="ISO-8601 timestamp of report generation")
    buckets: List[StalenessBucket] = Field(
        ...,
        description="Staleness buckets in order",
    )


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

def staleness_report_logic(db: Session) -> StalenessReportResponse:
    now = datetime.utcnow()
    t_1h = now - timedelta(hours=1)
    t_6h = now - timedelta(hours=6)
    t_24h = now - timedelta(hours=24)
    t_7d = now - timedelta(days=7)
    t_30d = now - timedelta(days=30)

    # Subquery: most recent scored_at per server
    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_score_date"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    result_buckets: List[StalenessBucket] = []

    # <1h
    rows = db.execute(
        select(latest_subq.c.server_id).where(latest_subq.c.latest_score_date > t_1h)
    ).fetchall()
    sids = [r[0] for r in rows]
    result_buckets.append(StalenessBucket(bucket="<1h", count=len(sids), examples=sids[:5]))

    # 1-6h
    rows = db.execute(
        select(latest_subq.c.server_id).where(
            latest_subq.c.latest_score_date <= t_1h,
            latest_subq.c.latest_score_date > t_6h,
        )
    ).fetchall()
    sids = [r[0] for r in rows]
    result_buckets.append(StalenessBucket(bucket="1-6h", count=len(sids), examples=sids[:5]))

    # 6-24h
    rows = db.execute(
        select(latest_subq.c.server_id).where(
            latest_subq.c.latest_score_date <= t_6h,
            latest_subq.c.latest_score_date > t_24h,
        )
    ).fetchall()
    sids = [r[0] for r in rows]
    result_buckets.append(StalenessBucket(bucket="6-24h", count=len(sids), examples=sids[:5]))

    # 1-7d
    rows = db.execute(
        select(latest_subq.c.server_id).where(
            latest_subq.c.latest_score_date <= t_24h,
            latest_subq.c.latest_score_date > t_7d,
        )
    ).fetchall()
    sids = [r[0] for r in rows]
    result_buckets.append(StalenessBucket(bucket="1-7d", count=len(sids), examples=sids[:5]))

    # 7-30d
    rows = db.execute(
        select(latest_subq.c.server_id).where(
            latest_subq.c.latest_score_date <= t_7d,
            latest_subq.c.latest_score_date > t_30d,
        )
    ).fetchall()
    sids = [r[0] for r in rows]
    result_buckets.append(StalenessBucket(bucket="7-30d", count=len(sids), examples=sids[:5]))

    # >30d: servers with scores older than 30d + servers never scored (outer join)
    rows_scored_old = db.execute(
        select(latest_subq.c.server_id).where(latest_subq.c.latest_score_date <= t_30d)
    ).fetchall()
    rows_unscored = db.execute(
        select(McpServerRegistry.server_id).outerjoin(
            latest_subq,
            McpServerRegistry.server_id == latest_subq.c.server_id,
        ).where(latest_subq.c.server_id.is_(None))
    ).fetchall()
    sids = [r[0] for r in rows_scored_old] + [r[0] for r in rows_unscored]
    result_buckets.append(StalenessBucket(bucket=">30d", count=len(sids), examples=sids[:5]))

    return StalenessReportResponse(generated_at=now, buckets=result_buckets)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/staleness-report",
    response_model=StalenessReportResponse,
    summary="Get score staleness report",
)
def staleness_report(
    db: Session = Depends(get_session),
) -> StalenessReportResponse:
    """
    Return a staleness report for all servers based on their most recent
    LLM axis score timestamp. Servers are bucketed by age of last score.
    Servers that have never been scored are included in the >30d bucket.
    """
    return staleness_report_logic(db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import timezone as TZ
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

    now = datetime.now(TZ.utc)

    # Seed: one server per bucket
    bucket_ages = [
        ("srv_1", timedelta(minutes=30)),   # <1h
        ("srv_2", timedelta(hours=2)),       # 1-6h
        ("srv_3", timedelta(hours=12)),      # 6-24h
        ("srv_4", timedelta(days=3)),         # 1-7d
        ("srv_5", timedelta(days=15)),        # 7-30d
        ("srv_6", timedelta(days=40)),       # >30d (scored)
    ]

    with TestSessionLocal() as sess:
        for idx, (srv_id, age) in enumerate(bucket_ages, start=1):
            sess.add(
                McpServerRegistry(
                    server_id=srv_id,
                    name=f"Server {srv_id}",
                    registry_source="self-test",
                    url=f"http://{srv_id}.example.com",
                    first_seen=now - timedelta(days=100),
                    last_seen=now - age,
                    last_scanned=now - age,
                    last_assessed=now - age,
                    risk_tier="low",
                    trust_score=0.9,
                    verdict="approved",
                    verdict_reasoning="test",
                    scan_count=1,
                    confidence=0.9,
                    meta=None,
                )
            )
            sess.add(
                McpLlmAxisScore(
                    id=idx,
                    server_id=srv_id,
                    axis_name="overall_risk",
                    model_version="v1",
                    decision_rule_version="r1",
                    adapter_sha256="deadbeef",
                    label="low",
                    label_index=0,
                    probs=None,
                    p_critical=0.0,
                    p_danger=0.1,
                    p_top=0.9,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now - age,
                )
            )
        # srv_7: registered but never scored → >30d
        sess.add(
            McpServerRegistry(
                server_id="srv_7",
                name="Server srv_7 (never scored)",
                registry_source="self-test",
                url="http://srv_7.example.com",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now,
                last_assessed=now,
                risk_tier="low",
                trust_score=0.9,
                verdict="approved",
                verdict_reasoning="test",
                scan_count=1,
                confidence=0.9,
                meta=None,
            )
        )
        sess.commit()

    client = TestClient(test_app)

    resp = client.get("/api/scoring/staleness-report")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    buckets = {b["bucket"]: b for b in data["buckets"]}

    expected_buckets = ["<1h", "1-6h", "6-24h", "1-7d", "7-30d", ">30d"]
    for eb in expected_buckets:
        assert eb in buckets, f"Missing bucket: {eb}"

    assert buckets["<1h"]["count"] == 1, f"<1h: expected 1, got {buckets['<1h']['count']}"
    assert buckets["1-6h"]["count"] == 1, f"1-6h: expected 1, got {buckets['1-6h']['count']}"
    assert buckets["6-24h"]["count"] == 1, f"6-24h: expected 1, got {buckets['6-24h']['count']}"
    assert buckets["1-7d"]["count"] == 1, f"1-7d: expected 1, got {buckets['1-7d']['count']}"
    assert buckets["7-30d"]["count"] == 1, f"7-30d: expected 1, got {buckets['7-30d']['count']}"
    # >30d: srv_6 (scored 40d ago) + srv_7 (never scored) = 2
    assert buckets[">30d"]["count"] == 2, f">30d: expected 2, got {buckets['>30d']['count']}"

    assert "srv_1" in buckets["<1h"]["examples"]
    assert "srv_2" in buckets["1-6h"]["examples"]
    assert "srv_3" in buckets["6-24h"]["examples"]
    assert "srv_4" in buckets["1-7d"]["examples"]
    assert "srv_5" in buckets["7-30d"]["examples"]
    assert "srv_6" in buckets[">30d"]["examples"]
    assert "srv_7" in buckets[">30d"]["examples"]

    print("PASS")
