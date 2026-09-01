from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class RecentScore(BaseModel):
    server_id: int
    name: str
    last_scored_at: datetime
    risk_tier: Optional[str] = None


class FreshnessResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    oldest_unscored_days: Optional[int] = None
    tier_distribution: Dict[str, int]
    recent_scores: List[RecentScore]


@router.get("/scoring/freshness", response_model=FreshnessResponse)
def get_freshness(session: Session = Depends(get_session)):
    # Subquery: latest scored_at per server
    latest_scores_subq = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Outer join servers with their latest score (if any)
    rows = (
        session.query(
            McpServerRegistry.id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            latest_scores_subq.c.last_scored_at,
        )
        .outerjoin(
            latest_scores_subq,
            McpServerRegistry.id == latest_scores_subq.c.server_id,
        )
        .all()
    )

    total_servers = len(rows)
    scored_servers = 0
    tier_distribution: Dict[str, int] = {}
    recent_scores: List[RecentScore] = []
    oldest_unscored_days: Optional[int] = None

    now = datetime.utcnow()
    unscored_days_list: List[int] = []

    for row in rows:
        server_id, name, risk_tier, last_scored_at = row
        if last_scored_at is not None:
            scored_servers += 1
            tier = risk_tier or "unknown"
            tier_distribution[tier] = tier_distribution.get(tier, 0) + 1
            recent_scores.append(
                RecentScore(
                    server_id=server_id,
                    name=name,
                    last_scored_at=last_scored_at,
                    risk_tier=risk_tier,
                )
            )
        else:
            # compute days since registration if possible
            created_at = getattr(row, "created_at", None)
            if created_at:
                days = (now - created_at).days
                unscored_days_list.append(days)

    unscored_servers = total_servers - scored_servers
    if unscored_days_list:
        oldest_unscored_days = max(unscored_days_list)

    # sort recent scores by most recent
    recent_scores.sort(key=lambda x: x.last_scored_at, reverse=True)

    return FreshnessResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        oldest_unscored_days=oldest_unscored_days,
        tier_distribution=tier_distribution,
        recent_scores=recent_scores,
    )


if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite DB
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # Seed data
    sess = SessionLocal()
    now = datetime.utcnow()
    servers = [
        McpServerRegistry(
            id=1,
            name="srv1",
            risk_tier="low",
            created_at=now,
        ),
        McpServerRegistry(
            id=2,
            name="srv2",
            risk_tier="medium",
            created_at=now,
        ),
        McpServerRegistry(
            id=3,
            name="srv3",
            risk_tier="high",
            created_at=now,
        ),
        McpServerRegistry(
            id=4,
            name="srv4",
            risk_tier="low",
            created_at=now,
        ),
        McpServerRegistry(
            id=5,
            name="srv5",
            risk_tier="medium",
            created_at=now,
        ),
    ]
    sess.add_all(servers)

    scores = [
        McpLlmAxisScore(server_id=1, scored_at=now),
        McpLlmAxisScore(server_id=2, scored_at=now),
        McpLlmAxisScore(server_id=3, scored_at=now),
    ]
    sess.add_all(scores)
    sess.commit()
    sess.close()

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)
    resp = client.get("/api/scoring/freshness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unscored_servers"] == 2
    assert data["tier_distribution"]
    print("PASS")