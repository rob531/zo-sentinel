# deps: fastapi, pydantic, sqlalchemy
"""Never Scored Backlog Rollup.

Provides GET /reporting/never-scored-backlog returning a snapshot of servers that
have never been scored, broken down by risk tier, with sample servers per tier.
Mirrors the structure of never_scored_burndown_api.py but for the current
backlog rather than a historical series.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["never_scored_backlog_rollup"])


class ServerSample(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    url: str | None


class TierBreakdown(BaseModel):
    risk_tier: str
    count: int
    sample_servers: List[ServerSample]


class NeverScoredBacklogResponse(BaseModel):
    as_of: str
    never_scored_count: int
    total_registry_count: int
    by_risk_tier: List[TierBreakdown]


@router.get("/reporting/never-scored-backlog", response_model=NeverScoredBacklogResponse)
def get_never_scored_backlog(
    sample_size: int = Query(default=5, ge=1, le=50),
    db: Session = Depends(get_session),
) -> NeverScoredBacklogResponse:
    """Return a snapshot of servers that have never been scored, by risk tier."""
    now = datetime.now(timezone.utc)

    total_servers: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    scored_subq = select(distinct(McpLlmAxisScore.server_id)).subquery()
    never_scored_q = (
        select(McpServerRegistry)
        .where(~McpServerRegistry.server_id.in_(scored_subq))
    )
    never_scored_count: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
        .where(~McpServerRegistry.server_id.in_(scored_subq))
    ).scalar_one() or 0

    never_scored_rows = db.execute(never_scored_q).scalars().all()

    by_tier: dict[str, List[McpServerRegistry]] = {}
    for srv in never_scored_rows:
        tier = srv.risk_tier or "UNKNOWN"
        by_tier.setdefault(tier, []).append(srv)

    tiers: List[TierBreakdown] = []
    for tier, servers in sorted(by_tier.items()):
        samples = [
            ServerSample(
                server_id=s.server_id,
                name=s.name,
                registry_source=s.registry_source,
                url=s.url,
            )
            for s in servers[:sample_size]
        ]
        tiers.append(TierBreakdown(risk_tier=tier, count=len(servers), sample_servers=samples))

    return NeverScoredBacklogResponse(
        as_of=now.isoformat(),
        never_scored_count=never_scored_count,
        total_registry_count=total_servers,
        by_risk_tier=tiers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = SessionLocal()
    for i, (sid, tier, src) in enumerate([
        ("srv1", "HIGH", "npm"),
        ("srv2", "HIGH", "npm"),
        ("srv3", "MEDIUM", "github"),
        ("srv4", "MEDIUM", "github"),
        ("srv5", "LOW", "github"),
    ], start=1):
        db.add(McpServerRegistry(server_id=sid, name=f"Srv {i}", risk_tier=tier, registry_source=src))
    db.commit()
    db.close()

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/api/reporting/never-scored-backlog?sample_size=2")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_registry_count"] == 5
    assert data["never_scored_count"] == 5
    assert len(data["by_risk_tier"]) == 3
    tier_map = {t["risk_tier"]: t for t in data["by_risk_tier"]}
    assert tier_map["HIGH"]["count"] == 2
    assert tier_map["HIGH"]["sample_servers"][0]["server_id"] == "srv1"
    print("PASS")
