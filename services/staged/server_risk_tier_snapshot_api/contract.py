from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierDistribution(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    UNCLASSIFIED: int = 0


class RiskTierSnapshotResponse(BaseModel):
    total_servers: int
    tier_distribution: TierDistribution
    as_of: str


@router.get("/api/risk/tier-snapshot", response_model=RiskTierSnapshotResponse)
def get_risk_tier_snapshot(db: Session = Depends(get_session)):
    query = text("""
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY risk_tier
    """)
    result = db.execute(query).fetchall()
    tiers: Dict[str, int] = {"TRUSTED_GENERAL": 0, "TRUSTED_RESEARCH": 0, "UNCLASSIFIED": 0}
    total = 0
    for row in result:
        tier = row[0] or "UNCLASSIFIED"
        count = row[1]
        total += count
        if tier in tiers:
            tiers[tier] = count
    return RiskTierSnapshotResponse(
        total_servers=total,
        tier_distribution=TierDistribution(**tiers),
        as_of=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    class TestMcpServerRegistry:
        __tablename__ = "mcp_server_registry"
        server_id: int
        risk_tier: str
        name: str

        def __init__(self, server_id: int, risk_tier: str, name: str):
            self.server_id = server_id
            self.risk_tier = risk_tier
            self.name = name

    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    test_session = sessionmaker(bind=test_engine)()

    def seed_server_registry(session: Session):
        session.execute(
            text("CREATE TABLE mcp_server_registry (server_id INTEGER PRIMARY KEY, risk_tier TEXT, name TEXT)")
        )
        servers = [
            (1, "UNCLASSIFIED", "server-a"),
            (2, "TRUSTED_GENERAL", "server-b"),
            (3, "TRUSTED_RESEARCH", "server-c"),
            (4, "TRUSTED_GENERAL", "server-d"),
            (5, "UNCLASSIFIED", "server-e"),
        ]
        for sid, tier, name in servers:
            session.execute(
                text("INSERT INTO mcp_server_registry (server_id, risk_tier, name) VALUES (:sid, :tier, :name)"),
                {"sid": sid, "tier": tier, "name": name},
            )
        session.commit()

    seed_server_registry(test_session)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return test_session

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    resp = client.get("/api/risk/tier-snapshot")
    data = resp.json()
    assert resp.status_code == 200
    dist = data["tier_distribution"]
    total = sum(dist.values())
    assert total == 5, f"Expected 5, got {total}"
    assert dist["TRUSTED_GENERAL"] == 2
    assert dist["TRUSTED_RESEARCH"] == 1
    assert dist["UNCLASSIFIED"] == 2
    assert "as_of" in data
    print("PASS")