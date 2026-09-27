from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])


class ServerFreshnessResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str]
    last_scanned: Optional[datetime]
    scan_count: int
    days_since_scan: int
    is_stale: bool
    is_ghost: bool
    freshness_score: int


class FreshnessResponse(BaseModel):
    servers: List[ServerFreshnessResponse]


def compute_freshness_score(days_since_scan: int, days_since_seen: int, is_stale: bool, is_ghost: bool) -> int:
    if is_ghost:
        return 0
    if is_stale:
        return 20
    base = 100 - (days_since_scan * 3) - (days_since_seen * 2)
    return max(0, min(100, base))


@router.get("/servers/freshness", response_model=FreshnessResponse)
def get_servers_freshness(session: Session = Depends(get_session)) -> FreshnessResponse:
    servers = session.query(McpServerRegistry).all()
    now = datetime.utcnow()
    results = []

    for server in servers:
        last_scanned = server.last_scanned or server.first_seen
        days_since_scan = (now - last_scanned).days if last_scanned else 999
        days_since_seen = (now - (server.last_seen or server.first_seen)).days
        is_stale = days_since_scan > 7
        is_ghost = days_since_seen > 30
        freshness_score = compute_freshness_score(days_since_scan, days_since_seen, is_stale, is_ghost)

        results.append(ServerFreshnessResponse(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            last_scanned=server.last_scanned,
            scan_count=server.scan_count,
            days_since_scan=days_since_scan,
            is_stale=is_stale,
            is_ghost=is_ghost,
            freshness_score=freshness_score,
        ))

    return FreshnessResponse(servers=results)


def compute_overall_freshness_score(session: Session = Depends(get_session)) -> int:
    freshness_data = get_servers_freshness(session)
    if not freshness_data.servers:
        return 100
    total = sum(s.freshness_score for s in freshness_data.servers)
    return total // len(freshness_data.servers)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    now = datetime.utcnow()

    s1 = McpServerRegistry(
        server_id="srv-001", name="Active Server", risk_tier="low",
        last_scanned=now - timedelta(days=1), last_seen=now - timedelta(days=1),
        scan_count=10, first_seen=now - timedelta(days=30),
        url="https://active.example.com", registry_source="test", confidence=0.9,
    )
    db.add(s1)

    s2 = McpServerRegistry(
        server_id="srv-002", name="Stale Server", risk_tier="medium",
        last_scanned=now - timedelta(days=10), last_seen=now - timedelta(days=10),
        scan_count=5, first_seen=now - timedelta(days=60),
        url="https://stale.example.com", registry_source="test", confidence=0.8,
    )
    db.add(s2)

    s3 = McpServerRegistry(
        server_id="srv-003", name="Ghost Server", risk_tier="high",
        last_scanned=now - timedelta(days=5), last_seen=now - timedelta(days=45),
        scan_count=2, first_seen=now - timedelta(days=100),
        url="https://ghost.example.com", registry_source="test", confidence=0.7,
    )
    db.add(s3)
    db.commit()
    db.close()

    client = TestClient(test_app)
    response = client.get("/api/servers/freshness")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    servers = data.get("servers", [])
    assert len(servers) == 3, f"Expected 3 servers, got {len(servers)}"

    server_map = {s["server_id"]: s for s in servers}

    s1_data = server_map["srv-001"]
    assert s1_data["is_stale"] is False, f"srv-001 should not be stale"
    assert s1_data["is_ghost"] is False, f"srv-001 should not be ghost"
    assert 50 <= s1_data["freshness_score"] <= 100, f"srv-001 score {s1_data['freshness_score']} out of range"

    s2_data = server_map["srv-002"]
    assert s2_data["is_stale"] is True, f"srv-002 should be stale"
    assert s2_data["freshness_score"] == 20, f"srv-002 should score 20, got {s2_data['freshness_score']}"

    s3_data = server_map["srv-003"]
    assert s3_data["is_ghost"] is True, f"srv-003 should be ghost"
    assert s3_data["freshness_score"] == 0, f"srv-003 should score 0, got {s3_data['freshness_score']}"

    print("PASS")
    exit(0)