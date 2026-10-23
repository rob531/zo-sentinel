from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/risk", tags=["risk"])


class TierDistributionItem(BaseModel):
    source: str
    tier: str
    count: int


class TierDistributionResponse(BaseModel):
    data: List[TierDistributionItem]


@router.get("/tier-distribution-by-source", response_model=TierDistributionResponse)
def get_risk_tier_distribution_by_source(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_session),
):
    """
    Returns risk tier distribution by registry source for a given date range.
    """
    query = """
        SELECT 
            registry_source as source,
            risk_tier as tier,
            COUNT(*) as count
        FROM mcp_server_registry
        WHERE 1=1
    """
    params = {}
    
    if start_date:
        query += " AND first_seen >= :start_date"
        params["start_date"] = start_date
    if end_date:
        query += " AND first_seen <= :end_date"
        params["end_date"] = end_date
    
    query += " GROUP BY registry_source, risk_tier ORDER BY registry_source, risk_tier"
    
    result = db.execute(text(query), params)
    rows = result.fetchall()
    
    data = [TierDistributionItem(source=row.source, tier=row.tier, count=row.count) for row in rows]
    return TierDistributionResponse(data=data)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    CreateTable = text("""
        CREATE TABLE mcp_server_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            registry_source TEXT,
            risk_tier TEXT,
            confidence REAL,
            description TEXT,
            first_seen TEXT,
            last_assessed TEXT,
            last_scanned TEXT,
            last_seen TEXT,
            meta TEXT,
            name TEXT,
            scan_count INTEGER,
            trust_score REAL,
            url TEXT,
            verdict TEXT,
            verdict_reasoning TEXT
        )
    """)
    
    with test_engine.connect() as conn:
        conn.execute(CreateTable)
        conn.commit()
        
        insert = text("""
            INSERT INTO mcp_server_registry (server_id, registry_source, risk_tier, first_seen)
            VALUES (:server_id, :registry_source, :risk_tier, :first_seen)
        """)
        
        test_data = [
            {"server_id": "srv1", "registry_source": "npm", "risk_tier": "high", "first_seen": "2024-01-01"},
            {"server_id": "srv2", "registry_source": "npm", "risk_tier": "high", "first_seen": "2024-01-02"},
            {"server_id": "srv3", "registry_source": "npm", "risk_tier": "medium", "first_seen": "2024-01-03"},
            {"server_id": "srv4", "registry_source": "github", "risk_tier": "low", "first_seen": "2024-01-04"},
            {"server_id": "srv5", "registry_source": "github", "risk_tier": "high", "first_seen": "2024-01-05"},
            {"server_id": "srv6", "registry_source": "pypi", "risk_tier": "medium", "first_seen": "2024-01-06"},
        ]
        
        for row in test_data:
            conn.execute(insert, row)
        conn.commit()
    
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    response = client.get("/api/risk/tier-distribution-by-source")
    assert response.status_code == 200
    
    data = response.json()["data"]
    assert len(data) == 5
    
    source_tier_counts = {(d["source"], d["tier"]): d["count"] for d in data}
    assert source_tier_counts[("npm", "high")] == 2
    assert source_tier_counts[("npm", "medium")] == 1
    assert source_tier_counts[("github", "low")] == 1
    assert source_tier_counts[("github", "high")] == 1
    assert source_tier_counts[("pypi", "medium")] == 1
    
    response_range = client.get(
        "/api/risk/tier-distribution-by-source",
        params={"start_date": "2024-01-01", "end_date": "2024-01-03"}
    )
    assert response_range.status_code == 200
    
    data_range = response_range.json()["data"]
    assert len(data_range) == 2
    
    print("PASS")
    sys.exit(0)