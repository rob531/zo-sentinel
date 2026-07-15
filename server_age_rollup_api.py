from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPServerRegistry
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import func, case

router = APIRouter()

class AgeBucket(BaseModel):
    label: str
    count: int
    by_tier: Dict[str, int]

class AgeRollupResponse(BaseModel):
    buckets: List[AgeBucket]

def get_age_bucket(server: MCPServerRegistry) -> str:
    now = datetime.utcnow()
    age = now - server.first_seen

    if age < timedelta(days=1):
        return "lt_1d"
    elif timedelta(days=1) <= age < timedelta(days=7):
        return "1d_7d"
    elif timedelta(days=7) <= age < timedelta(days=30):
        return "7d_30d"
    else:
        return "gt_30d"

@router.get("/servers/age-rollup", response_model=AgeRollupResponse)
async def get_server_age_rollup(db: Session = Depends(get_session)):
    servers = db.query(MCPServerRegistry).all()

    buckets = {
        "lt_1d": {"count": 0, "by_tier": {"low": 0, "medium": 0, "high": 0}},
        "1d_7d": {"count": 0, "by_tier": {"low": 0, "medium": 0, "high": 0}},
        "7d_30d": {"count": 0, "by_tier": {"low": 0, "medium": 0, "high": 0}},
        "gt_30d": {"count": 0, "by_tier": {"low": 0, "medium": 0, "high": 0}},
    }

    for server in servers:
        bucket = get_age_bucket(server)
        buckets[bucket]["count"] += 1
        risk_tier = server.risk_tier.lower()
        buckets[bucket]["by_tier"][risk_tier] += 1

    response_buckets = []
    for label, data in buckets.items():
        response_buckets.append(
            AgeBucket(
                label=label,
                count=data["count"],
                by_tier=data["by_tier"]
            )
        )

    return AgeRollupResponse(buckets=response_buckets)

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    test_session = SessionLocal()
    test_session.query(MCPServerRegistry).delete()
    test_session.add_all([
        MCPServerRegistry(
            server_id="server1",
            first_seen=datetime.utcnow() - timedelta(hours=12),
            last_seen=datetime.utcnow(),
            risk_tier="Low"
        ),
        MCPServerRegistry(
            server_id="server2",
            first_seen=datetime.utcnow() - timedelta(days=3),
            last_seen=datetime.utcnow(),
            risk_tier="Medium"
        ),
        MCPServerRegistry(
            server_id="server3",
            first_seen=datetime.utcnow() - timedelta(days=15),
            last_seen=datetime.utcnow(),
            risk_tier="High"
        ),
        MCPServerRegistry(
            server_id="server4",
            first_seen=datetime.utcnow() - timedelta(days=45),
            last_seen=datetime.utcnow(),
            risk_tier="Low"
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/servers/age-rollup")
    assert response.status_code == 200
    data = response.json()
    assert len(data["buckets"]) == 4
    print("PASS")