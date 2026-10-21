from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent, PerspectiveSnapshot
from .logic import build_events, build_members, build_perspective_list

router = APIRouter()


class PerspectiveResponse(BaseModel):
    id: int
    org_id: int
    name: str
    description: Optional[str] = None
    facet_filters: Any = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class PerspectiveMemberResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None


class PerspectiveEventResponse(BaseModel):
    server_id: str
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool = False
    created_at: datetime


@router.get("/api/perspectives", response_model=List[PerspectiveResponse])
def list_perspectives(session: Session = Depends(get_session)) -> List[dict]:
    perspectives = (
        session.execute(
            select(Perspective).order_by(Perspective.created_at.desc(), Perspective.id.desc())
        )
        .scalars()
        .all()
    )
    rows = [
        {
            "id": perspective.id,
            "org_id": perspective.org_id,
            "name": perspective.name,
            "description": perspective.description,
            "facet_filters": perspective.facet_filters,
            "created_by": perspective.created_by,
            "created_at": perspective.created_at,
            "updated_at": perspective.updated_at,
        }
        for perspective in perspectives
    ]
    return build_perspective_list(rows)


@router.get(
    "/api/perspectives/{perspective_id}/members",
    response_model=List[PerspectiveMemberResponse],
)
def list_perspective_members(
    perspective_id: int, session: Session = Depends(get_session)
) -> List[dict]:
    snapshot = session.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc(), PerspectiveSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if snapshot is None:
        return build_members([], [])

    membership = list(snapshot.membership or [])
    server_ids = [str(server_id) for server_id in membership]
    if not server_ids:
        return build_members(membership, [])

    registry_rows = (
        session.execute(
            select(McpServerRegistry).where(McpServerRegistry.server_id.in_(server_ids))
        )
        .scalars()
        .all()
    )
    rows = [
        {
            "server_id": registry.server_id,
            "name": registry.name,
            "risk_tier": registry.risk_tier,
            "trust_score": registry.trust_score,
            "verdict": registry.verdict,
        }
        for registry in registry_rows
    ]
    return build_members(membership, rows)


@router.get(
    "/api/perspectives/{perspective_id}/events",
    response_model=List[PerspectiveEventResponse],
)
def list_perspective_events(
    perspective_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> List[dict]:
    events = (
        session.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id == perspective_id)
            .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    rows = [
        {
            "server_id": event.server_id,
            "change_type": event.change_type,
            "old_tier": event.old_tier,
            "new_tier": event.new_tier,
            "seen": event.seen,
            "created_at": event.created_at,
        }
        for event in events
    ]
    return build_events(rows)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    now = datetime(2024, 1, 1, 12, 0, 0)
    with engine.begin() as connection:
        connection.execute(
            Perspective.__table__.insert(),
            [
                {
                    "id": 1,
                    "org_id": 1,
                    "name": "Perspective One",
                    "description": "First perspective",
                    "facet_filters": {},
                    "created_by": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": 2,
                    "org_id": 1,
                    "name": "Perspective Two",
                    "description": "Second perspective",
                    "facet_filters": {},
                    "created_by": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        connection.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "server_id": "server-1",
                    "name": "Server One",
                    "description": "First server",
                    "url": "http://server-1",
                    "registry_source": "test",
                    "risk_tier": "low",
                    "trust_score": 0.9,
                    "verdict": "trusted",
                    "verdict_reasoning": "test",
                    "confidence": 1.0,
                    "meta": {},
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now,
                    "scan_count": 1,
                },
                {
                    "server_id": "server-2",
                    "name": "Server Two",
                    "description": "Second server",
                    "url": "http://server-2",
                    "registry_source": "test",
                    "risk_tier": "medium",
                    "trust_score": 0.7,
                    "verdict": "conditional",
                    "verdict_reasoning": "test",
                    "confidence": 1.0,
                    "meta": {},
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now,
                    "scan_count": 1,
                },
                {
                    "server_id": "server-3",
                    "name": "Server Three",
                    "description": "Third server",
                    "url": "http://server-3",
                    "registry_source": "test",
                    "risk_tier": "high",
                    "trust_score": 0.4,
                    "verdict": "untrusted",
                    "verdict_reasoning": "test",
                    "confidence": 1.0,
                    "meta": {},
                    "first_seen": now,
                    "last_seen": now,
                    "last_scanned": now,
                    "last_assessed": now,
                    "scan_count": 1,
                },
            ],
        )
        connection.execute(
            PerspectiveSnapshot.__table__.insert(),
            {
                "id": 1,
                "perspective_id": 1,
                "membership": ["server-1", "server-2", "server-3"],
                "taken_at": now,
            },
        )
        connection.execute(
            PerspectiveEvent.__table__.insert(),
            [
                {
                    "id": 1,
                    "perspective_id": 1,
                    "server_id": "server-1",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "low",
                    "seen": False,
                    "created_at": datetime(2024, 1, 1, 10, 0, 0),
                },
                {
                    "id": 2,
                    "perspective_id": 1,
                    "server_id": "server-2",
                    "change_type": "changed",
                    "old_tier": "low",
                    "new_tier": "medium",
                    "seen": False,
                    "created_at": datetime(2024, 1, 1, 11, 0, 0),
                },
            ],
        )

    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        database = test_session()
        try:
            yield database
        finally:
            database.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        perspectives_response = client.get("/api/perspectives")
        assert perspectives_response.status_code == 200
        assert len(perspectives_response.json()) == 2

        members_response = client.get("/api/perspectives/1/members")
        assert members_response.status_code == 200
        members = members_response.json()
        assert len(members) == 3
        assert all("risk_tier" in member for member in members)

        events_response = client.get("/api/perspectives/1/events")
        assert events_response.status_code == 200
        events = events_response.json()
        assert len(events) == 2
        assert events[0]["created_at"] >= events[1]["created_at"]

    print("PASS")