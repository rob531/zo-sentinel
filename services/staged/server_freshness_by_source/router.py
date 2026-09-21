from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["server_freshness_by_source"])

class TierDistribution(BaseModel):
    tier: str
    count: int

class SourceFreshness(BaseModel):
    source: str
    total_servers: int
    scanned_last_7d: int
    scanned_last_30d: int
    never_scanned: int
    avg_days_since_scan: float
    tier_distribution: List[TierDistribution]

class FreshnessBySourceResponse(BaseModel):
    sources: List[SourceFreshness]

@router.get("/registry/freshness-by-source", response_model=FreshnessBySourceResponse)
def get_freshness_by_source(session: Session = Depends(get_session)):
    query = text("""
        SELECT
            registry_source,
            COUNT(*) as total_servers,
            SUM(CASE WHEN last_scanned >= CURRENT_DATE - INTERVAL '7 days' THEN 1 ELSE 0 END) as scanned_last_7d,
            SUM(CASE WHEN last_scanned >= CURRENT_DATE - INTERVAL '30 days' THEN 1 ELSE 0 END) as scanned_last_30d,
            SUM(CASE WHEN last_scanned IS NULL THEN 1 ELSE 0 END) as never_scanned,
            AVG(EXTRACT(EPOCH FROM (CURRENT_DATE - last_scanned::date)) / 86400) FILTER (WHERE last_scanned IS NOT NULL) as avg_days_since_scan
        FROM McpServerRegistry
        GROUP BY registry_source
    """)
    result = session.execute(query).fetchall()
    
    sources = []
    for row in result:
        tier_query = text("""
            SELECT risk_tier, COUNT(*) as cnt
            FROM McpServerRegistry
            WHERE registry_source = :source
            GROUP BY risk_tier
        """)
        tier_result = session.execute(tier_query, {"source": row.registry_source}).fetchall()
        tier_distribution = [TierDistribution(tier=t.risk_tier or "unknown", count=t.cnt) for t in tier_result]
        
        sources.append(SourceFreshness(
            source=row.registry_source,
            total_servers=row.total_servers,
            scanned_last_7d=row.scanned_last_7d,
            scanned_last_30d=row.scanned_last_30d,
            never_scanned=row.never_scanned,
            avg_days_since_scan=float(row.avg_days_since_scan) if row.avg_days_since_scan else 0.0,
            tier_distribution=tier_distribution
        ))
    
    return FreshnessBySourceResponse(sources=sources)


if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session
    
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            registry_source TEXT NOT NULL,
            last_scanned TEXT,
            scan_count INTEGER DEFAULT 0,
            risk_tier TEXT
        )
    """)
    
    now = datetime.now()
    servers = [
        ("srv1", "source_a", (now - timedelta(days=2)).isoformat(), 5, "high"),
        ("srv2", "source_a", (now - timedelta(days=3)).isoformat(), 3, "high"),
        ("srv3", "source_a", (now - timedelta(days=10)).isoformat(), 2, "medium"),
        ("srv4", "source_b", (now - timedelta(days=1)).isoformat(), 10, "low"),
        ("srv5", "source_b", None, 0, "low"),
        ("srv6", "source_b", (now - timedelta(days=40)).isoformat(), 1, "medium"),
        ("srv7", "source_c", (now - timedelta(days=20)).isoformat(), 4, "high"),
        ("srv8", "source_c", None, 0, "unknown"),
        ("srv9", "source_c", (now - timedelta(days=60)).isoformat(), 2, "low"),
        ("srv10", "source_c", (now - timedelta(days=5)).isoformat(), 6, "high"),
    ]
    
    for srv in servers:
        conn.execute(
            "INSERT INTO McpServerRegistry (server_id, registry_source, last_scanned, scan_count, risk_tier) VALUES (?, ?, ?, ?, ?)",
            srv
        )
    conn.commit()
    
    def override_get_session():
        return sqlite3.connect(":memory:", check_same_thread=False)
    
    from app.db import get_session
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/registry/freshness-by-source")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["sources"]) == 3, f"Expected 3 sources, got {len(data['sources'])}"
    
    source_b = next((s for s in data["sources"] if s["source"] == "source_b"), None)
    assert source_b is not None, "source_b not found"
    assert source_b["total_servers"] == 3, f"Expected 3 servers in source_b, got {source_b['total_servers']}"
    
    print("PASS")