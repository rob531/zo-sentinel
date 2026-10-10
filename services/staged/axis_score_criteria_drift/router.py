# services/staged/axis_score_criteria_drift/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Any, Dict, List

from app.db import get_session

router = APIRouter(prefix="/api", tags=["scoring"])


class TierBreakdown(BaseModel):
    tier: str
    count: int


class VersionDrift(BaseModel):
    version: str
    server_count: int
    tier_breakdown: Dict[str, int]


class DriftSummary(BaseModel):
    total_servers: int
    latest_version: str | None


class CriteriaDriftResponse(BaseModel):
    criteria_versions: List[VersionDrift] = Field(default_factory=list)
    summary: DriftSummary


@router.get("/scoring/criteria-drift", response_model=CriteriaDriftResponse)
def get_criteria_drift(session: Session = Depends(get_session)) -> Dict[str, Any]:
    stmt = text("""
        SELECT
            l.decision_rule_version AS version,
            r.risk_tier AS tier,
            COUNT(DISTINCT l.server_id) AS server_count,
            MIN(l.server_id) AS sample_server_id
        FROM mcp_llm_axis_scores l
        JOIN mcp_server_registry r ON l.server_id = r.server_id
        GROUP BY l.decision_rule_version, r.risk_tier
        ORDER BY l.decision_rule_version
    """)
    
    results = session.execute(stmt).all()
    
    versions_dict: Dict[str, Dict[str, Any]] = {}
    all_servers = set()
    
    for row in results:
        version = row.version
        tier = row.tier
        count = row.server_count
        sample_server_id = row.sample_server_id
        
        if version not in versions_dict:
            versions_dict[version] = {
                'version': version,
                'tier_breakdown': {},
                'server_count': 0
            }
        versions_dict[version]['tier_breakdown'][tier] = count
        versions_dict[version]['server_count'] += count
        if sample_server_id:
            all_servers.add(sample_server_id)
    
    criteria_versions = [
        VersionDrift(**v) for v in sorted(versions_dict.values(), key=lambda x: x['version'])
    ]
    
    latest = criteria_versions[-1] if criteria_versions else None
    
    return CriteriaDriftResponse(
        criteria_versions=criteria_versions,
        summary=DriftSummary(
            total_servers=len(all_servers),
            latest_version=latest.version if latest else None
        )
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                decision_rule_version TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER
            )
        """))
        conn.execute(text("INSERT INTO mcp_server_registry VALUES (:s, :n, :t)"),
                     [{"s": "s1", "n": "Server 1", "t": "low"},
                      {"s": "s2", "n": "Server 2", "t": "medium"},
                      {"s": "s3", "n": "Server 3", "t": "high"},
                      {"s": "s4", "n": "Server 4", "t": "critical"},
                      {"s": "s5", "n": "Server 5", "t": "critical"}])
        conn.execute(text("INSERT INTO mcp_llm_axis_scores (server_id, decision_rule_version) VALUES (:s, :v)"),
                     [{"s": "s1", "v": "v1"}, {"s": "s2", "v": "v1"}, {"s": "s3", "v": "v2"},
                      {"s": "s4", "v": "v2"}, {"s": "s5", "v": "v2"}, {"s": "s1", "v": "v3"},
                      {"s": "s2", "v": "v3"}, {"s": "s3", "v": "v3"}])
        conn.commit()

    from sqlalchemy.orm import sessionmaker
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

    client = TestClient(test_app)
    response = client.get("/api/scoring/criteria-drift")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert len(data["criteria_versions"]) == 3, f"Expected 3 versions, got {len(data['criteria_versions'])}"

    for version_data in data["criteria_versions"]:
        tier_breakdown = version_data.get("tier_breakdown", {})
        has_nonzero = any(count > 0 for count in tier_breakdown.values())
        assert has_nonzero, f"Version {version_data['version']} has no non-zero tier counts"

    print("PASS")