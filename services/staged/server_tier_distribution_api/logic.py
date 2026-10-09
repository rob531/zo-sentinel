from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TierDistribution(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    tiers: list[TierDistribution]
    total: int


def get_tier_distribution(
    risk_tier: Annotated[str, "filter by specific tier"] = None,
    session: Session = Depends(get_session),
) -> TierDistributionResponse:
    query = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label("count"),
    ).group_by(McpServerRegistry.risk_tier)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    results = query.all()

    total = sum(r.count for r in results)
    tiers = [
        TierDistribution(
            tier=r.risk_tier or "unknown",
            count=r.count,
            pct=round((r.count / total) * 100, 2) if total > 0 else 0.0,
        )
        for r in results
    ]

    return TierDistributionResponse(tiers=tiers, total=total)


def main():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    test_session = TestingSessionLocal()

    servers = [
        McpServerRegistry(
            server_id="srv-001",
            name="Critical Server 1",
            url="https://critical.example.com",
            registry_source="test",
            risk_tier="critical",
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="Critical Server 2",
            url="https://critical2.example.com",
            registry_source="test",
            risk_tier="critical",
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="Medium Server",
            url="https://medium.example.com",
            registry_source="test",
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="srv-004",
            name="Low Server 1",
            url="https://low1.example.com",
            registry_source="test",
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="srv-005",
            name="Low Server 2",
            url="https://low2.example.com",
            registry_source="test",
            risk_tier="low",
        ),
    ]
    test_session.add_all(servers)
    test_session.commit()

    tier_distribution = get_tier_distribution(session=test_session)

    assert len(tier_distribution.tiers) >= 3, (
        f"FAIL: tier_count={len(tier_distribution.tiers)}, expected >= 3"
    )
    assert tier_distribution.total == 5, (
        f"FAIL: total={tier_distribution.total}, expected 5"
    )

    print("PASS")


if __name__ == "__main__":
    app = FastAPI()

    @app.get("/api/fleet/tier-distribution")
    def read_tier_distribution(
        session: Session = Depends(get_session),
    ) -> TierDistributionResponse:
        return get_tier_distribution(session=session)

    main()