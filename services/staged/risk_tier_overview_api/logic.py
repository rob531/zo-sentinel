"""Risk tier overview service."""

from typing import Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_risk_tier_overview(session: Session) -> Dict:
    """Return {total_servers, by_tier: {tier: count}}."""
    total = session.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    tier_rows = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    by_tier = {row[0]: row[1] for row in tier_rows if row[0] is not None}

    return {"total_servers": total, "by_tier": by_tier}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    session = TestSession()
    servers = [
        McpServerRegistry(server_id=f"s{i}", risk_tier=tier, name=f"server{i}")
        for i, tier in enumerate(
            ["low", "medium", "high", "low", "medium", "high", "low", "medium", "low", "medium"]
        )
    ]
    session.add_all(servers)
    session.commit()

    result = get_risk_tier_overview(session)
    session.close()
    engine.dispose()

    total = result["total_servers"]
    by_tier = result["by_tier"]

    assert total == 10, f"total_servers expected 10, got {total}"
    assert by_tier.get("medium") == 4, f"by_tier['medium'] expected 4, got {by_tier.get('medium')}"

    app = FastAPI()
    that_app = app

    @app.get("/api/risk/overview")
    def _overview():
        return result

    from fastapi.testclient import TestClient

    that_app.dependency_overrides[get_session] = lambda: TestSession()
    client = TestClient(that_app)

    response = client.get("/api/risk/overview")
    assert response.status_code == 200, f"expected 200, got {response.status_code}"
    data = response.json()
    assert data["total_servers"] == 10
    assert data["by_tier"]["medium"] == 4

    print("PASS")