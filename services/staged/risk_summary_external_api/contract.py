from typing import Annotated
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


app = FastAPI(title="risk_summary_external_api")


@app.get("/risk/summary")
def get_risk_summary(db: Session = Depends(get_session)):
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar()
    tier_rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    return {
        "total_servers": total,
        "by_tier": {tier: count for tier, count in tier_rows}
    }


if __name__ == "__main__":
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

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    db = TestingSessionLocal()
    tiers = ["critical", "high", "medium", "low", "informational"]
    for i in range(10):
        tier = tiers[i % 5]
        db.add(McpServerRegistry(
            server_id=f"s{i}",
            name=f"Server {i}",
            url=f"https://server{i}.example.com",
            risk_tier=tier,
            registry_source="test",
            confidence=0.9,
            description="Test server",
            first_seen=None,
            last_assessed=None,
            last_scanned=None,
            last_seen=None,
            meta="{}",
            scan_count=0,
            trust_score=0.5,
            verdict="unknown",
            verdict_reasoning="Test",
        ))
    db.commit()
    db.close()

    response = client.get("/risk/summary")
    assert response.status_code == 200
    data = response.json()

    assert data["total_servers"] == 10

    db = TestingSessionLocal()
    tier_counts = {}
    rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    for tier, count in rows:
        tier_counts[tier] = count
    db.close()

    for tier, count in tier_counts.items():
        assert data["by_tier"].get(tier) == count, f"Mismatch for tier {tier}: expected {count}, got {data['by_tier'].get(tier)}"

    print("PASS")