from datetime import date
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierDistributionItem(BaseModel):
    source: str
    tier: str
    count: int


class TierDistributionResponse(BaseModel):
    data: List[TierDistributionItem]


@router.get("/api/risk/tier-distribution-by-source", response_model=TierDistributionResponse)
def get_tier_distribution_by_source(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    session: Session = Depends(get_session),
) -> TierDistributionResponse:
    query = session.query(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label("count"),
    ).group_by(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )

    if start_date:
        query = query.filter(McpServerRegistry.first_seen >= start_date)
    if end_date:
        query = query.filter(McpServerRegistry.first_seen <= end_date)

    results = query.all()

    return TierDistributionResponse(
        data=[
            TierDistributionItem(source=r.registry_source, tier=r.risk_tier, count=r.count)
            for r in results
        ]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    db = TestingSessionLocal()
    test_servers = [
        McpServerRegistry(
            server_id="srv-001",
            registry_source="source_a",
            risk_tier="high",
            first_seen=date(2024, 1, 15),
        ),
        McpServerRegistry(
            server_id="srv-002",
            registry_source="source_a",
            risk_tier="medium",
            first_seen=date(2024, 1, 16),
        ),
        McpServerRegistry(
            server_id="srv-003",
            registry_source="source_b",
            risk_tier="low",
            first_seen=date(2024, 1, 17),
        ),
        McpServerRegistry(
            server_id="srv-004",
            registry_source="source_b",
            risk_tier="high",
            first_seen=date(2024, 1, 18),
        ),
        McpServerRegistry(
            server_id="srv-005",
            registry_source="source_a",
            risk_tier="high",
            first_seen=date(2024, 1, 19),
        ),
    ]
    db.add_all(test_servers)
    db.commit()
    db.close()

    response = client.get("/api/risk/tier-distribution-by-source")
    assert response.status_code == 200

    data = response.json()["data"]
    counts = {(d["source"], d["tier"]): d["count"] for d in data}

    assert counts.get(("source_a", "high")) == 2
    assert counts.get(("source_a", "medium")) == 1
    assert counts.get(("source_b", "low")) == 1
    assert counts.get(("source_b", "high")) == 1

    print("PASS")