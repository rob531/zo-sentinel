from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from typing import Any
from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool


def create_router() -> APIRouter:
    router = APIRouter()

    class TierDriftResponse(BaseModel):
        perspective_id: int
        days: int
        transitions: list[dict[str, Any]]
        current_membership: dict[str, int]

    @router.get(
        "/perspectives/{perspective_id}/tier-drift",
        response_model=TierDriftResponse,
    )
    def get_tier_drift(
        perspective_id: int,
        days: int,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        query = text("""
            SELECT
                DATE(pe.created_at) as drift_date,
                pe.old_tier,
                pe.new_tier,
                COUNT(*) as transition_count
            FROM perspective_events pe
            WHERE pe.perspective_id = :perspective_id
              AND pe.created_at >= DATE('now', '-' || :days || ' days')
              AND pe.old_tier IS NOT NULL
              AND pe.new_tier IS NOT NULL
            GROUP BY DATE(pe.created_at), pe.old_tier, pe.new_tier
            ORDER BY drift_date
        """)
        result = session.execute(query, {"perspective_id": perspective_id, "days": days})
        transitions = [
            {
                "date": str(row[0]),
                "old_tier": row[1],
                "new_tier": row[2],
                "count": row[3],
            }
            for row in result.fetchall()
        ]

        snapshot = (
            session.query(PerspectiveSnapshot)
            .filter(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(PerspectiveSnapshot.taken_at.desc())
            .first()
        )
        membership = snapshot.membership if snapshot else {}

        return {
            "perspective_id": perspective_id,
            "days": days,
            "transitions": transitions,
            "current_membership": membership,
        }

    return router


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(), prefix="/api")
    return app


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER NOT NULL,
                server_id INTEGER,
                old_tier TEXT,
                new_tier TEXT,
                change_type TEXT,
                seen INTEGER,
                created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE perspective_snapshots (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER NOT NULL,
                membership TEXT,
                taken_at TIMESTAMP
            )
        """))

    session = SessionLocal()
    from datetime import datetime, timedelta

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    p1 = perspective_id = 1

    session.add(
        PerspectiveEvent(
            id=1,
            perspective_id=p1,
            server_id=100,
            old_tier="trusted",
            new_tier="research",
            change_type="tier_change",
            seen=True,
            created_at=datetime.combine(yesterday, datetime.min.time()),
        )
    )
    session.add(
        PerspectiveEvent(
            id=2,
            perspective_id=p1,
            server_id=101,
            old_tier="research",
            new_tier="controlled",
            change_type="tier_change",
            seen=True,
            created_at=datetime.combine(today, datetime.min.time()),
        )
    )
    session.add(
        PerspectiveEvent(
            id=3,
            perspective_id=p1,
            server_id=102,
            old_tier="controlled",
            new_tier="high_risk",
            change_type="tier_change",
            seen=True,
            created_at=datetime.combine(today, datetime.min.time()),
        )
    )
    session.add(
        PerspectiveSnapshot(
            id=1,
            perspective_id=p1,
            membership={"trusted": 2, "research": 1, "controlled": 1, "caution": 0, "high_risk": 1, "insufficient": 0},
            taken_at=datetime.combine(today, datetime.min.time()),
        )
    )
    session.commit()
    session.close()

    app = create_app()

    def override_get_session():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/perspectives/1/tier-drift?days=7")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["perspective_id"] == 1
    assert data["days"] == 7
    assert len(data["transitions"]) > 0, "transitions array is empty"

    expected_pairs = {(str(yesterday), "trusted", "research"), (str(today), "research", "controlled"), (str(today), "controlled", "high_risk")}
    found = {(t["date"], t["old_tier"], t["new_tier"]) for t in data["transitions"]}
    for pair in expected_pairs:
        assert pair in found, f"Expected transition {pair} not found in {found}"

    assert data["current_membership"]["trusted"] == 2
    assert data["current_membership"]["high_risk"] == 1

    print("PASS")