from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api/scores", tags=["scores"])


class RecentScoreChange(BaseModel):
    server_id: str
    server_name: str
    axis_name: str
    current_label: str
    current_p_top: float
    p_critical: float
    scored_at: datetime
    risk_tier: str
    escalated: bool

    class Config:
        from_attributes = True


def get_recent_score_changes(
    db: Session,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    limit = min(limit, 200)

    stmt = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
    )
    results = db.execute(stmt).all()

    return [
        {
            "server_id": axis.server_id,
            "server_name": srv.name,
            "axis_name": axis.axis_name,
            "current_label": axis.label,
            "current_p_top": axis.p_top,
            "p_critical": axis.p_critical,
            "scored_at": axis.scored_at,
            "risk_tier": srv.risk_tier,
            "escalated": bool(axis.escalated),
        }
        for axis, srv in results
    ]


@router.get("/recent-changes", response_model=list[RecentScoreChange])
def get_recent_score_changes_endpoint(
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[dict]:
    return get_recent_score_changes(db=db, days=days, limit=limit)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from main import app

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    db = TestingSessionLocal()
    srv1 = McpServerRegistry(
        server_id="srv1", name="Server One", risk_tier="low", registry_source="test"
    )
    srv2 = McpServerRegistry(
        server_id="srv2", name="Server Two", risk_tier="medium", registry_source="test"
    )
    srv3 = McpServerRegistry(
        server_id="srv3", name="Server Three", risk_tier="high", registry_source="test"
    )
    db.add_all([srv1, srv2, srv3])
    db.commit()

    now = datetime.now(timezone.utc)
    scores = [
        McpLlmAxisScore(
            server_id="srv1",
            axis_name="test_axis",
            label="A",
            p_top=0.8,
            p_critical=0.1,
            scored_at=now - timedelta(days=1),
            escalated=False,
        ),
        McpLlmAxisScore(
            server_id="srv2",
            axis_name="test_axis",
            label="B",
            p_top=0.6,
            p_critical=0.2,
            scored_at=now - timedelta(days=6),
            escalated=False,
        ),
        McpLlmAxisScore(
            server_id="srv3",
            axis_name="test_axis",
            label="C",
            p_top=0.4,
            p_critical=0.3,
            scored_at=now - timedelta(days=30),
            escalated=False,
        ),
        McpLlmAxisScore(
            server_id="srv1",
            axis_name="other_axis",
            label="D",
            p_top=0.2,
            p_critical=0.4,
            scored_at=now - timedelta(days=30),
            escalated=False,
        ),
        McpLlmAxisScore(
            server_id="srv2",
            axis_name="other_axis",
            label="E",
            p_top=0.1,
            p_critical=0.5,
            scored_at=now - timedelta(days=30),
            escalated=False,
        ),
    ]
    db.add_all(scores)
    db.commit()
    db.close()

    response = client.get("/api/scores/recent-changes?days=7&limit=50")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    body = response.json()
    assert len(body) == 2, f"Expected 2 results, got {len(body)}"

    for item in body:
        assert "scored_at" in item
        scored_at_str = item["scored_at"]
        if scored_at_str.endswith("Z"):
            scored_at_str = scored_at_str[:-1] + "+00:00"
        scored_at = datetime.fromisoformat(scored_at_str)
        assert scored_at >= now - timedelta(days=7), f"scored_at {scored_at} not within window"

    scored_ats = []
    for item in body:
        scored_at_str = item["scored_at"]
        if scored_at_str.endswith("Z"):
            scored_at_str = scored_at_str[:-1] + "+00:00"
        scored_ats.append(datetime.fromisoformat(scored_at_str))
    assert scored_ats == sorted(scored_ats, reverse=True), "Results not sorted DESC"

    print("PASS")