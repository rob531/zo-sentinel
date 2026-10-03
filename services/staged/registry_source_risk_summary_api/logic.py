from typing import Any
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpServerRegistry

RISK_TIERS = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "UNTRUSTED_GENERAL", "UNTRUSTED_RESEARCH", "UNKNOWN"]


class TierDistribution(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    UNTRUSTED_GENERAL: int = 0
    UNTRUSTED_RESEARCH: int = 0
    UNKNOWN: int = 0


class SourceSummary(BaseModel):
    source: str
    total_servers: int
    tier_distribution: TierDistribution
    avg_trust_score: float


class RiskSummaryResponse(BaseModel):
    sources: list[SourceSummary]


def get_risk_summary(db: Session) -> RiskSummaryResponse:
    stmt = (
        select(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("total_servers"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
        )
        .group_by(McpServerRegistry.registry_source)
    )
    results = db.execute(stmt).all()

    sources = []
    for row in results:
        source = row.registry_source
        total_servers = row.total_servers
        avg_trust_score = float(row.avg_trust_score) if row.avg_trust_score else 0.0

        tier_stmt = (
            select(
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("count"),
            )
            .where(McpServerRegistry.registry_source == source)
            .group_by(McpServerRegistry.risk_tier)
        )
        tier_results = db.execute(tier_stmt).all()

        tier_counts = {tier: 0 for tier in RISK_TIERS}
        for tier_row in tier_results:
            if tier_row.risk_tier in tier_counts:
                tier_counts[tier_row.risk_tier] = tier_row.count

        tier_distribution = TierDistribution(**tier_counts)

        sources.append(
            SourceSummary(
                source=source,
                total_servers=total_servers,
                tier_distribution=tier_distribution,
                avg_trust_score=round(avg_trust_score, 2),
            )
        )

    return RiskSummaryResponse(sources=sources)


def compute_risk_summary(session: Session = Depends(get_session)) -> RiskSummaryResponse:
    return get_risk_summary(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()

    @app.get("/api/registry/sources/risk-summary")
    def risk_summary_endpoint(session: Session = Depends(override_get_session)):
        return get_risk_summary(session)

    db = TestingSessionLocal()
    db.add(McpServerRegistry(
        server_id="srv-001",
        registry_source="github",
        risk_tier="TRUSTED_GENERAL",
        trust_score=85.0,
        name="test-server-1",
    ))
    db.add(McpServerRegistry(
        server_id="srv-002",
        registry_source="github",
        risk_tier="TRUSTED_RESEARCH",
        trust_score=90.0,
        name="test-server-2",
    ))
    db.add(McpServerRegistry(
        server_id="srv-003",
        registry_source="npm",
        risk_tier="UNTRUSTED_GENERAL",
        trust_score=30.0,
        name="test-server-3",
    ))
    db.add(McpServerRegistry(
        server_id="srv-004",
        registry_source="npm",
        risk_tier="UNKNOWN",
        trust_score=50.0,
        name="test-server-4",
    ))
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/registry/sources/risk-summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    sources = {s["source"]: s for s in data["sources"]}

    assert "github" in sources, "github source not found"
    assert "npm" in sources, "npm source not found"

    for source_name, source_data in sources.items():
        total = source_data["total_servers"]
        tier_sum = sum(source_data["tier_distribution"].values())
        assert total == tier_sum, f"{source_name}: total {total} != tier sum {tier_sum}"

    print("PASS")