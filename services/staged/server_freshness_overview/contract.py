# services/staged/server_freshness_overview/contract.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked here)
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class BucketCounts(BaseModel):
    fresh: int = Field(..., description="Servers with a score < 24h old")
    aging: int = Field(..., description="Servers with a score 24‑72h old")
    stale: int = Field(..., description="Servers with a score > 72h old")
    never_scored: int = Field(..., description="Servers with no scores")


class ServerFreshnessOverviewResponse(BaseModel):
    total_servers: int = Field(..., description="Number of servers in the registry")
    scored_servers: int = Field(..., description="Servers that have at least one score")
    bucket_counts: BucketCounts
    recent_pct: float = Field(..., description="Percent of all servers that are fresh")
    generated_at: datetime = Field(..., description="Timestamp of response generation")


def _hours_since(ts: datetime) -> float:
    """Return hours elapsed between now (UTC) and the supplied timestamp."""
    now = datetime.now(timezone.utc)
    delta = now - ts.replace(tzinfo=timezone.utc)
    return delta.total_seconds() / 3600.0


def _categorise(hours: float) -> str:
    if hours < 24:
        return "fresh"
    if hours < 72:
        return "aging"
    return "stale"


@router.get(
    "/server/freshness",
    response_model=ServerFreshnessOverviewResponse,
    summary="Freshness overview for all registered servers",
)
def get_server_freshness_overview(session: Session = Depends(get_session)):
    # ------------------------------------------------------------------
    # Gather latest score timestamp per server (if any)
    # ------------------------------------------------------------------
    latest_score_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    stmt = (
        select(
            McpServerRegistry.server_id,
            latest_score_subq.c.last_scored_at,
        )
        .outerjoin(
            latest_score_subq,
            McpServerRegistry.server_id == latest_score_subq.c.server_id,
        )
    )

    rows = session.execute(stmt).all()

    total_servers = len(rows)
    bucket: Dict[str, int] = {"fresh": 0, "aging": 0, "stale": 0, "never_scored": 0}
    scored_servers = 0

    for server_id, last_scored_at in rows:
        if last_scored_at is None:
            bucket["never_scored"] += 1
            continue

        scored_servers += 1
        hrs = _hours_since(last_scored_at)
        cat = _categorise(hrs)
        bucket[cat] += 1

    recent_pct = (bucket["fresh"] / total_servers * 100.0) if total_servers else 0.0

    response = ServerFreshnessOverviewResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        bucket_counts=BucketCounts(**bucket),
        recent_pct=round(recent_pct, 2),
        generated_at=datetime.now(timezone.utc),
    )
    return response


# ----------------------------------------------------------------------
# Self‑test (run with `python -m services.staged.server_freshness_overview.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db import Base  # type: ignore  # Base is the declarative base used by the app

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data
    now = datetime.now(timezone.utc)

    servers = [
        McpServerRegistry(server_id="srv_fresh_1", name="fresh1", registry_source="test"),
        McpServerRegistry(server_id="srv_fresh_2", name="fresh2", registry_source="test"),
        McpServerRegistry(server_id="srv_aging", name="aging", registry_source="test"),
        McpServerRegistry(server_id="srv_stale", name="stale", registry_source="test"),
        McpServerRegistry(server_id="srv_never", name="never", registry_source="test"),
    ]

    scores = [
        # fresh (<24h)
        McpLlmAxisScore(
            server_id="srv_fresh_1",
            axis_name="test",
            scored_at=now - timedelta(hours=5),
            adapter_sha256="a",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="low",
            label_index=0,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
        ),
        McpLlmAxisScore(
            server_id="srv_fresh_2",
            axis_name="test",
            scored_at=now - timedelta(hours=10),
            adapter_sha256="b",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="low",
            label_index=0,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
        ),
        # aging (24‑72h)
        McpLlmAxisScore(
            server_id="srv_aging",
            axis_name="test",
            scored_at=now - timedelta(hours=30),
            adapter_sha256="c",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="low",
            label_index=0,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
        ),
        # stale (>72h)
        McpLlmAxisScore(
            server_id="srv_stale",
            axis_name="test",
            scored_at=now - timedelta(hours=100),
            adapter_sha256="d",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=4,
            label="low",
            label_index=0,
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
        ),
    ]

    with SessionLocal() as db:
        db.add_all(servers)
        db.add_all(scores)
        db.commit()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def _override_get_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    resp = client.get("/api/server/freshness")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    expected_counts = {"fresh": 2, "aging": 1, "stale": 1, "never_scored": 1}
    if data["bucket_counts"] != expected_counts:
        print(f"FAIL: bucket_counts {data['bucket_counts']} != {expected_counts}", file=sys.stderr)
        sys.exit(1)

    if round(data["recent_pct"], 2) != 40.0:
        print(f"FAIL: recent_pct {data['recent_pct']} != 40.0", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)