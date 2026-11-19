"""Logic layer for the dispute tier conflict API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


def get_conflicts(
    db: Session,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return disputes whose proposed risk tier differs from the registry tier."""
    bounded_limit = max(1, min(int(limit), 200))

    query = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_axes,
            McpScoreDispute.created_at,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(
            McpScoreDispute.proposed_overall_risk.is_not(None),
            McpServerRegistry.risk_tier.is_not(None),
            func.lower(McpScoreDispute.proposed_overall_risk)
            != func.lower(McpServerRegistry.risk_tier),
        )
        .order_by(McpScoreDispute.created_at.desc())
        .limit(bounded_limit)
    )

    if status is not None:
        query = query.where(McpScoreDispute.status == status)

    rows = db.execute(query).all()
    return [
        {
            "dispute_id": row.id,
            "server_id": row.server_id,
            "name": row.name,
            "current_risk_tier": row.risk_tier,
            "proposed_overall_risk": row.proposed_overall_risk,
            "status": row.status,
            "reason_category": row.reason_category,
            "proposed_axes": row.proposed_axes,
            "created_at": row.created_at,
        }
        for row in rows
    ]


# Keep the common naming variants available to the thin router layer.
list_conflicts = get_conflicts
list_dispute_tier_conflicts = get_conflicts


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Server 1", risk_tier="high"),
                McpServerRegistry(server_id="server-2", name="Server 2", risk_tier="low"),
                McpServerRegistry(server_id="server-3", name="Server 3", risk_tier=None),
            ]
        )
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-1",
                    proposed_overall_risk="high",
                    proposed_axes=None,
                    status="open",
                    reason_category="tier_review",
                    created_at=now,
                    submitted_by="tester",
                    explanation="matches registry tier",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-2",
                    proposed_overall_risk="high",
                    proposed_axes={"overall": "high"},
                    status="open",
                    reason_category="model_conflict",
                    created_at=now,
                    submitted_by="tester",
                    explanation="differs from registry tier",
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-3",
                    proposed_overall_risk="high",
                    proposed_axes=None,
                    status="open",
                    reason_category="model_conflict",
                    created_at=now,
                    submitted_by="tester",
                    explanation="registry tier is unavailable",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/conflicts")
    def conflicts_endpoint(
        status: Optional[str] = None,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        conflicts = get_conflicts(db, status=status, limit=limit)
        return {"status": status, "count": len(conflicts), "conflicts": conflicts}

    from fastapi.testclient import TestClient

    client = TestClient(that_app)
    response = client.get("/api/disputes/conflicts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["conflicts"][0]["dispute_id"] == 2
    assert payload["conflicts"][0]["current_risk_tier"] == "low"
    assert payload["conflicts"][0]["proposed_overall_risk"] == "high"

    filtered = client.get("/api/disputes/conflicts?status=open")
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1
    assert filtered.json()["conflicts"][0]["status"] == "open"

    print("PASS")