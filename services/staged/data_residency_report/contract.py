"""Data Residency Report Service - Contract"""
from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


class TierCount(BaseModel):
    tier: str
    count: int


class SourceReport(BaseModel):
    source: str
    count: int
    by_tier: dict[str, int]


class DataResidencyReportResponse(BaseModel):
    sources: list[SourceReport]


def get_data_residency_report(session: Session) -> DataResidencyReportResponse:
    """Fetch data residency report grouped by registry_source and risk_tier."""
    query = text("""
        SELECT 
            registry_source,
            risk_tier,
            COUNT(*) as cnt
        FROM mcp_server_registry
        WHERE registry_source IS NOT NULL
        GROUP BY registry_source, risk_tier
        ORDER BY registry_source, risk_tier
    """)
    result = session.execute(query)
    rows = result.fetchall()
    
    sources_dict: dict[str, dict[str, int]] = {}
    for row in rows:
        source = row[0]
        tier = row[1] if row[1] else "unknown"
        count = row[2]
        
        if source not in sources_dict:
            sources_dict[source] = {"total": 0}
        sources_dict[source]["total"] += count
        sources_dict[source][tier] = count
    
    sources = []
    for source, tier_data in sorted(sources_dict.items()):
        total = tier_data.pop("total")
        by_tier = dict(sorted(tier_data.items()))
        sources.append(SourceReport(
            source=source,
            count=total,
            by_tier=by_tier
        ))
    
    return DataResidencyReportResponse(sources=sources)


@router.get("/api/fleet/data-residency-report", response_model=DataResidencyReportResponse)
def data_residency_report_endpoint(session: Session = Depends(get_session)) -> DataResidencyReportResponse:
    """GET /api/fleet/data-residency-report - Returns registry sources grouped by risk tier."""
    return get_data_residency_report(session)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    create_tables = text("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            confidence REAL,
            description TEXT,
            first_seen TEXT,
            last_assessed TEXT,
            last_scanned TEXT,
            last_seen TEXT,
            meta TEXT,
            name TEXT,
            registry_source TEXT,
            risk_tier TEXT,
            scan_count INTEGER,
            server_id TEXT,
            trust_score REAL,
            url TEXT,
            verdict TEXT,
            verdict_reasoning TEXT
        )
    """)
    with test_engine.connect() as conn:
        conn.execute(create_tables)
        conn.commit()
    
    SessionLocal = sessionmaker(bind=test_engine)
    
    seed_data = [
        ("github", "critical"),
        ("github", "high"),
        ("github", "medium"),
        ("docker_hub", "critical"),
        ("docker_hub", "high"),
        ("docker_hub", "high"),
        ("npm_registry", "low"),
        ("npm_registry", "low"),
        ("npm_registry", "low"),
    ]
    
    insert_sql = text("""
        INSERT INTO mcp_server_registry 
        (registry_source, risk_tier, name, url, server_id, first_seen, last_seen)
        VALUES 
        (:source, :tier, :name, :url, :server_id, :first_seen, :last_seen)
    """)
    
    with test_engine.connect() as conn:
        for i, (src, tier) in enumerate(seed_data):
            conn.execute(insert_sql, {
                "source": src,
                "tier": tier,
                "name": f"server_{i}",
                "url": f"https://{src}/server/{i}",
                "server_id": f"id_{i}",
                "first_seen": "2024-01-01",
                "last_seen": "2024-01-01"
            })
        conn.commit()
    
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/fleet/data-residency-report")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    sources = data.get("sources", [])
    
    assert len(sources) == 3, f"Expected 3 sources, got {len(sources)}"
    
    source_names = {s["source"] for s in sources}
    assert source_names == {"github", "docker_hub", "npm_registry"}, f"Unexpected sources: {source_names}"
    
    docker_hub_source = next(s for s in sources if s["source"] == "docker_hub")
    assert docker_hub_source["count"] == 3, f"Expected docker_hub count 3, got {docker_hub_source['count']}"
    assert docker_hub_source["by_tier"].get("high") == 2, f"Expected docker_hub high=2, got {docker_hub_source['by_tier']}"
    
    print("PASS")
    sys.exit(0)