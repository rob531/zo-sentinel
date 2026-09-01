import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must stay unchanged)
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class RecentScore(BaseModel):
    server_id: int
    name: str
    last_scored_at: datetime.datetime
    risk_tier: str


class FreshnessResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    oldest_unscored_days: int
    tier_distribution: Dict[str, int]
    recent_scores: List[RecentScore]


@router.get("/scoring/freshness", response_model=FreshnessResponse)
def get_freshness(session: Session = Depends(get_session)):
    now = datetime.datetime.utcnow()

    # Total servers
    total_servers = session.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()

    # Servers that have at least one score
    scored_subq = (
        select(McpLlmAxisScore.server_id).distinct().subquery()
    )
    scored_servers = session.execute(
        select(func.count()).select_from(McpServerRegistry).where(McpServerRegistry.id.in_(scored_subq))
    ).scalar_one()

    unscored_servers = total_servers - scored_servers

    # Oldest unscored server (days since its creation)
    oldest_unscored_days = 0
    if unscored_servers:
        oldest = session.execute(
            select(func.max(func.julianday(now) - func.julianday(McpServerRegistry.created_at))).where(
                ~McpServerRegistry.id.in_(scored_subq)
            )
        ).scalar_one()
        oldest_unscored_days = int(oldest) if oldest is not None else 0

    # Tier distribution
    tier_rows = session.execute(
        select(McpServerRegistry.risk_tier, func.count()).group_by(McpServerRegistry.risk_tier)
    ).all()
    tier_distribution = {tier: cnt for tier, cnt in tier_rows}

    # Recent scores per server (latest scored_at)
    latest_scores_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at")
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    recent_rows = session.execute(
        select(
            McpServerRegistry.id,
            McpServerRegistry.name,
            latest_scores_subq.c.last_scored_at,
            McpServerRegistry.risk_tier,
        )
        .join(latest_scores_subq, McpServerRegistry.id == latest_scores_subq.c.server_id)
    ).all()
    recent_scores = [
        RecentScore(
            server_id=row[0],
            name=row[1],
            last_scored_at=row[2],
            risk_tier=row[3],
        )
        for row in recent_rows
    ]

    return FreshnessResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        oldest_unscored_days=oldest_unscored_days,
        tier_distribution=tier_distribution,
        recent_scores=recent_scores,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.scoring_freshness_dashboard.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create a throw‑away SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import Base from the real app to create tables
    from app.db import Base  # noqa: E402

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Override the dependency
    def get_test_session() -> Session:
        return TestSession()

    router.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSession() as sess:
        now = datetime.datetime.utcnow()
        # 5 servers
        servers = [
            McpServerRegistry(id=1, name="srv‑a", risk_tier="high", created_at=now - datetime.timedelta(days=30)),
            McpServerRegistry(id=2, name="srv‑b", risk_tier="medium", created_at=now - datetime.timedelta(days=20)),
            McpServerRegistry(id=3, name="srv‑c", risk_tier="low", created_at=now - datetime.timedelta(days=10)),
            McpServerRegistry(id=4, name="srv‑d", risk_tier="high", created_at=now - datetime.timedelta(days=5)),
            McpServerRegistry(id=5, name="srv‑e", risk_tier="medium", created_at=now - datetime.timedelta(days=2)),
        ]
        sess.add_all(servers)
        # 3 scored servers (1,2,3)
        scores = [
            McpLlmAxisScore(id=1, server_id=1, scored_at=now - datetime.timedelta(days=1), score=0.9),
            McpLlmAxisScore(id=2, server_id=2, scored_at=now - datetime.timedelta(days=2), score=0.7),
            McpLlmAxisScore(id=3, server_id=3, scored_at=now - datetime.timedelta(days=3), score=0.5),
        ]
        sess.add_all(scores)
        sess.commit()

    client = TestClient(router)

    resp = client.get("/api/scoring/freshness")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()
    assert data["unscored_servers"] == 2, "expected 2 unscored servers"
    assert data["tier_distribution"], "tier distribution should not be empty"

    print("PASS")