from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierSnapshotResponse(BaseModel):
    total_servers: int
    tier_distribution: Dict[str, int]
    as_of: str


@router.get("/api/risk/tier-snapshot")
async def get_tier_snapshot(session: Session = Depends(get_session)) -> TierSnapshotResponse:
    results = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.risk_tier).all()
    
    tier_distribution = {tier: count for tier, count in results}
    total = sum(tier_distribution.values())
    
    return TierSnapshotResponse(
        total_servers=total,
        tier_distribution=tier_distribution,
        as_of=datetime.now(timezone.utc).isoformat()
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base, McpServerRegistry

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=test_engine)
    
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()
    
    servers = [
        McpServerRegistry(server_id=f"s{i}", name=f"Server {i}", risk_tier=tier, url=f"http://server{i}.com", registry_source="test", trust_score=0.5, confidence=0.5, scan_count=0, first_seen=datetime.utcnow(), last_seen=datetime.utcnow(), last_scanned=datetime.utcnow(), last_assessed=datetime.utcnow())
        for i, tier in enumerate(["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ELEVATED", "GENERAL", "ELEVATED"])
    ]
    test_session.add_all(servers)
    test_session.commit()
    
    def override_get_session():
        yield test_session
    
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/api/risk/tier-snapshot")
    
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 5
    assert sum(data["tier_distribution"].values()) == 5
    print("PASS")