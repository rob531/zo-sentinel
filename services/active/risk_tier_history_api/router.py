from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter()


class TierTransition(BaseModel):
    date: str
    old_tier: Optional[str]
    new_tier: Optional[str]


class ServerRiskTierHistory(BaseModel):
    server_id: str
    name: str
    transitions: list[TierTransition]


class ServerSummary(BaseModel):
    server_id: str
    name: str
    current_tier: Optional[str]
    transition_count: int
    first_seen_at: Optional[str]


class GlobalRiskTierHistory(BaseModel):
    servers: list[ServerSummary]


@router.get("/api/servers/{server_id}/risk-tier-history", response_model=ServerRiskTierHistory)
def get_risk_tier_history(server_id: str, session: Session = Depends(get_session)) -> ServerRiskTierHistory:
    server_stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    server_result = session.execute(server_stmt).scalar_one_or_none()

    if not server_result:
        return ServerRiskTierHistory(server_id=server_id, name="", transitions=[])

    events_stmt = (
        select(PerspectiveEvent)
        .where(PerspectiveEvent.server_id == server_id)
        .where(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at)
    )
    events = session.execute(events_stmt).scalars().all()

    transitions = [
        TierTransition(
            date=event.created_at.isoformat() if event.created_at else "",
            old_tier=event.old_tier,
            new_tier=event.new_tier,
        )
        for event in events
    ]

    return ServerRiskTierHistory(
        server_id=server_id,
        name=server_result.name or "",
        transitions=transitions,
    )


@router.get("/api/risk/tier-history", response_model=GlobalRiskTierHistory)
def get_global_risk_tier_history(session: Session = Depends(get_session)) -> GlobalRiskTierHistory:
    servers_stmt = select(McpServerRegistry)
    servers = session.execute(servers_stmt).scalars().all()

    server_summaries = []
    for server in servers:
        count_stmt = (
            select(func.count())
            .select_from(PerspectiveEvent)
            .where(PerspectiveEvent.server_id == server.server_id)
            .where(PerspectiveEvent.change_type == "tier_change")
        )
        count = session.execute(count_stmt).scalar() or 0

        server_summaries.append(
            ServerSummary(
                server_id=server.server_id,
                name=server.name or "",
                current_tier=server.risk_tier,
                transition_count=count,
                first_seen_at=server.first_seen.isoformat() if server.first_seen else None,
            )
        )

    return GlobalRiskTierHistory(servers=server_summaries)


def get_risk_tier_transitions(server_id: str, session: Session) -> list[dict]:
    events_stmt = (
        select(PerspectiveEvent)
        .where(PerspectiveEvent.server_id == server_id)
        .where(PerspectiveEvent.change_type == "tier_change")
        .order_by(PerspectiveEvent.created_at)
    )
    events = session.execute(events_stmt).scalars().all()
    return [
        {
            "date": event.created_at.isoformat() if event.created_at else None,
            "old_tier": event.old_tier,
            "new_tier": event.new_tier,
        }
        for event in events
    ]


def get_server_by_name(name: str, session: Session) -> Optional[dict]:
    stmt = select(McpServerRegistry).where(McpServerRegistry.name == name)
    server = session.execute(stmt).scalar_one_or_none()
    if not server:
        return None
    return {
        "server_id": server.server_id,
        "name": server.name,
        "risk_tier": server.risk_tier,
    }


if __name__ == "__main__":
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as db:
        server1 = McpServerRegistry(
            server_id="srv-001",
            name="alpha-server",
            risk_tier="medium",
            first_seen=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        server2 = McpServerRegistry(
            server_id="srv-002",
            name="beta-server",
            risk_tier="high",
            first_seen=datetime(2024, 1, 15, tzinfo=timezone.utc),
        )
        db.add(server1)
        db.add(server2)
        db.flush()

        event1 = PerspectiveEvent(
            server_id="srv-001",
            change_type="tier_change",
            old_tier="low",
            new_tier="medium",
            created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            seen=True,
        )
        event2 = PerspectiveEvent(
            server_id="srv-001",
            change_type="tier_change",
            old_tier="medium",
            new_tier="high",
            created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            seen=True,
        )
        event3 = PerspectiveEvent(
            server_id="srv-002",
            change_type="tier_change",
            old_tier="low",
            new_tier="high",
            created_at=datetime(2024, 2, 15, tzinfo=timezone.utc),
            seen=True,
        )
        db.add_all([event1, event2, event3])
        db.commit()

    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    resp_hist1 = client.get("/api/servers/srv-001/risk-tier-history")
    assert resp_hist1.status_code == 200, f"Expected 200, got {resp_hist1.status_code}"
    data_hist1 = resp_hist1.json()
    assert len(data_hist1["transitions"]) == 2, f"Expected 2 transitions for srv-001, got {len(data_hist1['transitions'])}"
    assert data_hist1["server_id"] == "srv-001"

    resp_hist2 = client.get("/api/servers/srv-002/risk-tier-history")
    assert resp_hist2.status_code == 200
    data_hist2 = resp_hist2.json()
    assert len(data_hist2["transitions"]) == 1

    resp_global = client.get("/api/risk/tier-history")
    assert resp_global.status_code == 200
    data_global = resp_global.json()
    assert len(data_global["servers"]) == 2
    for srv in data_global["servers"]:
        assert srv["transition_count"] > 0, f"Expected non-zero count for {srv['server_id']}"

    print("PASS")
