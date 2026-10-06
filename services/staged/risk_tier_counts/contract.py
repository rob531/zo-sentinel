from typing import List
from pydantic import BaseModel
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

router = None
create_app = None
get_session = None

def _init():
    global router, create_app, get_session
    if router is not None:
        return
    
    from app.db import get_session as _gs
    get_session = _gs
    
    from fastapi import APIRouter
    router = APIRouter()
    
    class TierCount(BaseModel):
        tier: str
        count: int
    
    class TierCountsResponse(BaseModel):
        tiers: List[TierCount]
    
    def get_risk_tier_counts(session: Session = Depends(get_session)):
        result = session.execute(
            text("""
                SELECT risk_tier, COUNT(*) as count
                FROM mcp_server_registry
                GROUP BY risk_tier
                ORDER BY risk_tier
            """)
        )
        rows = result.fetchall()
        tiers = [TierCount(tier=row[0], count=row[1]) for row in rows]
        return TierCountsResponse(tiers=tiers)
    
    @router.get("/risk/tier-counts", response_model=TierCountsResponse)
    def endpoint(session: Session = Depends(get_session)):
        return get_risk_tier_counts(session)
    
    app = FastAPI()
    app.include_router(router, prefix="/api")
    
    def _create_app():
        return app
    
    create_app = _create_app

_init()

if __name__ == "__main__":
    from app.db import get_session as _gs
    get_session = _gs
    
    from fastapi import APIRouter
    from sqlalchemy.pool import StaticPool
    
    router = APIRouter()
    
    class TierCount(BaseModel):
        tier: str
        count: int
    
    class TierCountsResponse(BaseModel):
        tiers: List[TierCount]
    
    def get_risk_tier_counts(session: Session):
        result = session.execute(
            text("""
                SELECT risk_tier, COUNT(*) as count
                FROM mcp_server_registry
                GROUP BY risk_tier
                ORDER BY risk_tier
            """)
        )
        rows = result.fetchall()
        tiers = [TierCount(tier=row[0], count=row[1]) for row in rows]
        return TierCountsResponse(tiers=tiers)
    
    @router.get("/risk/tier-counts", response_model=TierCountsResponse)
    def endpoint(session: Session = Depends(get_session)):
        return get_risk_tier_counts(session)
    
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                meta TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER
            )
        """))
        
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, risk_tier)
            VALUES ('srv1', 'Test Server 1', 'https://example1.com', 'test', 'TRUSTED_GENERAL')
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, risk_tier)
            VALUES ('srv2', 'Test Server 2', 'https://example2.com', 'test', 'HIGH_RISK_ISOLATED')
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, risk_tier)
            VALUES ('srv3', 'Test Server 3', 'https://example3.com', 'test', 'CAUTION_LIMITED')
        """))
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    response = client.get("/api/risk/tier-counts")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data.get("tiers", [])) >= 3, f"Expected >= 3 tiers, got {len(data.get('tiers', []))}"
    
    print("PASS")