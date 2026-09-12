"""Service: perspective_snapshot_history_api"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspectives"])


class MembershipCounts(BaseModel):
    trusted_count: int = 0
    research_count: int = 0
    enterprise_count: int = 0
    caution_count: int = 0
    high_risk_count: int = 0
    known_threat_count: int = 0
    insufficient_count: int = 0
    total_servers: int = 0


class SnapshotEntry(BaseModel):
    taken_at: datetime
    membership: MembershipCounts


class PerspectiveHistoryResponse(BaseModel):
    perspective_id: int
    name: str
    history: list[SnapshotEntry]


def get_perspective_history(
    perspective_id: int,
    limit: int = 30,
    session: Session = Depends(get_session),
) -> PerspectiveHistoryResponse:
    """Read perspective snapshots joined to perspectives."""
    result = session.execute(
        text("""
            SELECT p.id, p.name, ps.taken_at, ps.membership
            FROM perspectives p
            JOIN perspective_snapshots ps ON ps.perspective_id = p.id
            WHERE p.id = :perspective_id
            ORDER BY ps.taken_at DESC
            LIMIT :limit
        """),
        {"perspective_id": perspective_id, "limit": limit},
    )
    rows = result.fetchall()

    history = []
    for row in rows:
        membership_data = json.loads(row.membership) if isinstance(row.membership, str) else row.membership
        history.append(
            SnapshotEntry(
                taken_at=row.taken_at,
                membership=MembershipCounts(**membership_data),
            )
        )

    return PerspectiveHistoryResponse(
        perspective_id=rows[0].id if rows else perspective_id,
        name=rows[0].name if rows else "",
        history=history,
    )


@router.get("/perspectives/{perspective_id}/history", response_model=PerspectiveHistoryResponse)
def get_history(
    perspective_id: int,
    limit: int = Query(default=30, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> PerspectiveHistoryResponse:
    return get_perspective_history(perspective_id, limit, session)


def create_tables(engine):
    """Create tables for in-memory test."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS perspectives (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                org_id INTEGER,
                description TEXT,
                facet_filters TEXT,
                created_by INTEGER,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS perspective_snapshots (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER NOT NULL,
                taken_at TIMESTAMP NOT NULL,
                membership TEXT NOT NULL
            )
        """))
        conn.commit()


def seed_test_data(session: Session):
    """Seed 1 perspective with 3 snapshots."""
    now = datetime.utcnow()
    session.execute(
        text("""
            INSERT INTO perspectives (id, name, org_id, description, created_at, updated_at)
            VALUES (:id, :name, :org_id, :description, :created_at, :updated_at)
        """),
        {
            "id": 1,
            "name": "Test Perspective",
            "org_id": 1,
            "description": "Test description",
            "created_at": now,
            "updated_at": now,
        },
    )
    session.execute(
        text("""
            INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
            VALUES (:id, :perspective_id, :taken_at, :membership)
        """),
        {
            "id": 1,
            "perspective_id": 1,
            "taken_at": now,
            "membership": json.dumps({
                "trusted_count": 10,
                "research_count": 5,
                "enterprise_count": 20,
                "caution_count": 3,
                "high_risk_count": 2,
                "known_threat_count": 1,
                "insufficient_count": 4,
                "total_servers": 45,
            }),
        },
    )
    session.execute(
        text("""
            INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
            VALUES (:id, :perspective_id, :taken_at, :membership)
        """),
        {
            "id": 2,
            "perspective_id": 1,
            "taken_at": now,
            "membership": json.dumps({
                "trusted_count": 12,
                "research_count": 6,
                "enterprise_count": 22,
                "caution_count": 4,
                "high_risk_count": 1,
                "known_threat_count": 2,
                "insufficient_count": 3,
                "total_servers": 50,
            }),
        },
    )
    session.execute(
        text("""
            INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
            VALUES (:id, :perspective_id, :taken_at, :membership)
        """),
        {
            "id": 3,
            "perspective_id": 1,
            "taken_at": now,
            "membership": json.dumps({
                "trusted_count": 15,
                "research_count": 7,
                "enterprise_count": 25,
                "caution_count": 5,
                "high_risk_count": 3,
                "known_threat_count": 1,
                "insufficient_count": 2,
                "total_servers": 58,
            }),
        },
    )
    session.commit()


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_tables(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        seed_test_data(session)

    client = TestClient(app)
    response = client.get("/api/perspectives/1/history")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert len(data["history"]) == 3, f"Expected 3 snapshots, got {len(data['history'])}"

    total_servers = data["history"][0]["membership"]["total_servers"]
    assert total_servers > 0, f"Expected total_servers > 0, got {total_servers}"

    print("PASS")
    exit(0)