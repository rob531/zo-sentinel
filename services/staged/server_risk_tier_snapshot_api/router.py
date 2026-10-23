from datetime import datetime, timezone
from typing import Dict
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

router = APIRouter(prefix="/api/risk", tags=["risk"])


class TierSnapshotResponse(BaseModel):
    total_servers: int
    tier_distribution: Dict[str, int]
    as_of: str


@router.get("/tier-snapshot", response_model=TierSnapshotResponse)
def get_tier_snapshot(session: Session = Depends(get_session)) -> TierSnapshotResponse:
    """
    Get snapshot of server risk tier distribution.
    Reads mcp_server_registry, aggregates servers by current risk_tier.
    """
    result = session.execute(
        text("""
            SELECT risk_tier, COUNT(*) as count
            FROM mcp_server_registry
            WHERE risk_tier IS NOT NULL
            GROUP BY risk_tier
        """)
    )
    
    rows = result.fetchall()
    
    tier_distribution: Dict[str, int] = {}
    total_servers = 0
    
    for row in rows:
        tier = row[0]
        count = row[1]
        tier_distribution[tier] = count
        total_servers += count
    
    as_of = datetime.now(timezone.utc).isoformat()
    
    return TierSnapshotResponse(
        total_servers=total_servers,
        tier_distribution=tier_distribution,
        as_of=as_of
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    # Create in-memory SQLite database for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                description TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.commit()
    
    # Seed 5 servers with known tiers
    test_servers = [
        {"server_id": "srv-001", "name": "Server Alpha", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "srv-002", "name": "Server Beta", "risk_tier": "TRUSTED_GENERAL"},
        {"server_id": "srv-003", "name": "Server Gamma", "risk_tier": "TRUSTED_RESEARCH"},
        {"server_id": "srv-004", "name": "Server Delta", "risk_tier": "UNTRUSTED"},
        {"server_id": "srv-005", "name": "Server Epsilon", "risk_tier": "EVALUATION"},
    ]
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    with engine.connect() as conn:
        for server in test_servers:
            conn.execute(
                text("""
                    INSERT INTO mcp_server_registry 
                    (server_id, name, risk_tier, url, registry_source, trust_score, confidence, verdict, description, first_seen, last_seen, scan_count)
                    VALUES 
                    (:server_id, :name, :risk_tier, 'https://example.com', 'test', 0.5, 0.5, 'pending', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
                """),
                server
            )
        conn.commit()
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Create test client with dependency override
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/api/risk/tier-snapshot")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    total = data["total_servers"]
    distribution = data["tier_distribution"]
    
    # Assert tier_distribution sums to 5
    sum_of_tiers = sum(distribution.values())
    assert sum_of_tiers == 5, f"Expected sum of 5, got {sum_of_tiers}"
    
    print("PASS")