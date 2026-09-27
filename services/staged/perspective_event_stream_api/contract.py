"""Perspective Event Stream API contract."""

from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent


router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: int
    server_name: str
    perspective_id: int
    change_type: str
    old_tier: int | None
    new_tier: int | None
    seen: bool
    created_at: datetime


class PerspectiveEventsListResponse(BaseModel):
    events: list[PerspectiveEventResponse]


def get_perspective_events(
    session: Session,
    perspective_id: int,
    server_id: int | None = None,
    change_type: str | None = None,
    limit: int = 50,
) -> list[PerspectiveEventResponse]:
    """Fetch perspective events joined with server registry."""
    if limit > 500:
        limit = 500
    if limit < 1:
        limit = 1

    query = """
        SELECT 
            pe.id,
            pe.server_id,
            msr.name as server_name,
            pe.perspective_id,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.seen,
            pe.created_at
        FROM perspective_events pe
        JOIN McpServerRegistry msr ON pe.server_id = msr.server_id
        WHERE pe.perspective_id = :perspective_id
    """
    params = {"perspective_id": perspective_id, "limit": limit}

    if server_id is not None:
        query += " AND pe.server_id = :server_id"
        params["server_id"] = server_id

    if change_type is not None:
        query += " AND pe.change_type = :change_type"
        params["change_type"] = change_type

    query += " ORDER BY pe.created_at DESC LIMIT :limit"

    result = session.execute(text(query), params)
    rows = result.fetchall()

    return [
        PerspectiveEventResponse(
            id=row.id,
            server_id=row.server_id,
            server_name=row.server_name,
            perspective_id=row.perspective_id,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            seen=row.seen,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/perspectives/{perspective_id}/events", response_model=PerspectiveEventsListResponse)
def get_events(
    perspective_id: int,
    server_id: Annotated[int | None, Query(description="Filter by server_id")] = None,
    change_type: Annotated[
        str | None,
        Query(description="Filter by change_type: tier_up, tier_down, added, removed"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Result limit")] = 50,
    session: Session = Depends(get_session),
) -> PerspectiveEventsListResponse:
    """Get events for a specific perspective."""
    events = get_perspective_events(
        session=session,
        perspective_id=perspective_id,
        server_id=server_id,
        change_type=change_type,
        limit=limit,
    )
    return PerspectiveEventsListResponse(events=events)


def create_tables(engine):
    """Create required tables in the database."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                risk_tier INTEGER,
                confidence FLOAT,
                trust_score FLOAT,
                last_seen TIMESTAMP,
                first_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS perspectives (
                id INTEGER PRIMARY KEY,
                org_id INTEGER,
                name TEXT,
                description TEXT,
                facet_filters TEXT,
                created_by INTEGER,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER,
                server_id INTEGER,
                change_type TEXT,
                old_tier INTEGER,
                new_tier INTEGER,
                seen BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP
            )
        """))
        conn.commit()


def seed_data(session: Session):
    """Seed test data: 2 perspectives and 4 events."""
    session.execute(
        text("DELETE FROM perspective_events"),
    )
    session.execute(
        text("DELETE FROM perspectives"),
    )
    session.execute(
        text("DELETE FROM McpServerRegistry"),
    )

    session.execute(
        text("""
            INSERT INTO McpServerRegistry (server_id, name, url, description, risk_tier)
            VALUES 
                (1, 'server-alpha', 'http://alpha.example.com', 'Alpha server', 2),
                (2, 'server-beta', 'http://beta.example.com', 'Beta server', 3),
                (3, 'server-gamma', 'http://gamma.example.com', 'Gamma server', 1)
        """),
    )

    session.execute(
        text("""
            INSERT INTO perspectives (id, org_id, name, description, created_by, created_at)
            VALUES 
                (1, 100, 'perspective-1', 'First perspective', 1, CURRENT_TIMESTAMP),
                (2, 100, 'perspective-2', 'Second perspective', 1, CURRENT_TIMESTAMP)
        """),
    )

    now = datetime.utcnow()
    session.execute(
        text("""
            INSERT INTO perspective_events 
                (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
            VALUES 
                (1, 1, 1, 'tier_up', 1, 2, false, :ts1),
                (2, 1, 2, 'tier_down', 3, 2, false, :ts2),
                (3, 1, 3, 'added', NULL, 1, true, :ts3),
                (4, 2, 1, 'tier_up', 2, 3, false, :ts4)
        """),
        {
            "ts1": now,
            "ts2": datetime.utcnow(),
            "ts3": datetime.utcnow(),
            "ts4": datetime.utcnow(),
        },
    )
    session.commit()


def main():
    """Self-test runner."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    create_tables(engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    seed_data(TestingSessionLocal())

    from fastapi import FastAPI
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)

    response = client.get("/api/perspectives/1/events")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    events = data["events"]
    assert len(events) == 3, f"Expected 3 events for perspective 1, got {len(events)}"

    for event in events:
        if event["change_type"] == "tier_up":
            assert event["old_tier"] is not None, "tier_up event must have old_tier"
            assert event["new_tier"] is not None, "tier_up event must have new_tier"
            assert event["old_tier"] < event["new_tier"], (
                f"tier_up event old_tier ({event['old_tier']}) must be < new_tier ({event['new_tier']})"
            )

    tier_up_events = [e for e in events if e["change_type"] == "tier_up"]
    assert len(tier_up_events) >= 1, "Expected at least 1 tier_up event"

    print("PASS")


if __name__ == "__main__":
    main()