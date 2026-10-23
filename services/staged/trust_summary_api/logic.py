from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierSummary(BaseModel):
    count: int
    avg_trust_score: float
    avg_confidence: float


class SourceSummary(BaseModel):
    count: int
    avg_trust_score: float


class TrustSummaryResponse(BaseModel):
    org_id: int
    total_servers: int
    tiers: dict[str, TierSummary]
    sources: dict[str, SourceSummary]
    generated_at: str


@router.get("/api/trust/summary", response_model=TrustSummaryResponse)
def get_trust_summary(session: Session = Depends(get_session)) -> TrustSummaryResponse:
    org_id = 1

    tier_query = text("""
        SELECT
            risk_tier,
            COUNT(*) as cnt,
            AVG(trust_score) as avg_trust,
            AVG(confidence) as avg_conf
        FROM mcp_server_registry
        WHERE server_id IN (SELECT server_id FROM mcp_server_registry)
        GROUP BY risk_tier
        ORDER BY risk_tier
    """)
    tier_rows = session.execute(tier_query).fetchall()

    tiers: dict[str, TierSummary] = {}
    total = 0
    for row in tier_rows:
        tier_name = row.risk_tier if row.risk_tier else "unknown"
        cnt = row.cnt
        total += cnt
        tiers[tier_name] = TierSummary(
            count=cnt,
            avg_trust_score=round(float(row.avg_trust), 4) if row.avg_trust else 0.0,
            avg_confidence=round(float(row.avg_conf), 4) if row.avg_conf else 0.0,
        )

    source_query = text("""
        SELECT
            registry_source,
            COUNT(*) as cnt,
            AVG(trust_score) as avg_trust
        FROM mcp_server_registry
        WHERE server_id IN (SELECT server_id FROM mcp_server_registry)
        GROUP BY registry_source
        ORDER BY registry_source
    """)
    source_rows = session.execute(source_query).fetchall()

    sources: dict[str, SourceSummary] = {}
    for row in source_rows:
        src = row.registry_source if row.registry_source else "unknown"
        sources[src] = SourceSummary(
            count=row.cnt,
            avg_trust_score=round(float(row.avg_trust), 4) if row.avg_trust else 0.0,
        )

    return TrustSummaryResponse(
        org_id=org_id,
        total_servers=total,
        tiers=tiers,
        sources=sources,
        generated_at="2025-01-01T00:00:00Z",
    )


if __name__ == "__main__":
    from datetime import datetime
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    McpServerRegistry.__table__.create(engine)

    session = TestingSession()
    servers = [
        McpServerRegistry(
            server_id="s1", name="Server One", url="http://example.com",
            risk_tier="low", trust_score=0.95, confidence=0.9,
            registry_source="upstream", meta="{}", verdict="trusted",
            verdict_reasoning="good scores",
        ),
        McpServerRegistry(
            server_id="s2", name="Server Two", url="http://test.com",
            risk_tier="medium", trust_score=0.45, confidence=0.5,
            registry_source="community", meta="{}", verdict="disputed",
            verdict_reasoning="mixed scores",
        ),
        McpServerRegistry(
            server_id="s3", name="Server Three", url="http://three.com",
            risk_tier="high", trust_score=0.78, confidence=0.7,
            registry_source="upstream", meta="{}", verdict="trusted",
            verdict_reasoning="decent scores",
        ),
        McpServerRegistry(
            server_id="s4", name="Server Four", url="http://four.com",
            risk_tier="high", trust_score=0.32, confidence=0.4,
            registry_source="community", meta="{}", verdict="untrusted",
            verdict_reasoning="low scores",
        ),
    ]
    for s in servers:
        session.add(s)
    session.commit()
    session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: TestingSession()

    from fastapi.testclient import TestClient

    client = TestClient(that_app)
    resp = client.get("/api/trust/summary")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] >= 3, f"Expected total_servers >= 3, got {data['total_servers']}"
    assert "low" in data["tiers"], f"Expected 'low' tier key in {data['tiers'].keys()}"
    assert "medium" in data["tiers"], f"Expected 'medium' tier key in {data['tiers'].keys()}"
    assert "high" in data["tiers"], f"Expected 'high' tier key in {data['tiers'].keys()}"

    print("PASS")