# deps: requests
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_tier_snapshot"])


def get_risk_tier_snapshot(session: Session) -> Dict[str, Any]:
    """Query risk tier distribution across all servers."""
    results = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count")
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    distribution = {row.risk_tier: row.count for row in results}
    total_servers = sum(distribution.values())
    return {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "distribution": distribution,
    }


@router.get("/risk/snapshot")
def get_snapshot(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_risk_tier_snapshot(session)


def get_tier_distribution(session: Session) -> Dict[str, int]:
    """Return tier distribution dict for dependent services."""
    return get_risk_tier_snapshot(session)["distribution"]


def send_heartbeat(service_name: str, session: Session) -> None:
    """Placeholder heartbeat for dependent service compatibility."""
    pass


def get_server_history(server_id: str, session: Session) -> list:
    """Placeholder for server history lookup."""
    return []


def api_get_dwell_time(server_id: str, session: Session) -> float:
    """Placeholder for dwell time calculation."""
    return 0.0


def get_signal_scores_distribution(session: Session) -> Dict[str, Any]:
    """Placeholder for signal scores distribution."""
    return {}


def health_summary(session: Session) -> Dict[str, Any]:
    """Placeholder health summary."""
    return {}


def server_freshness(session: Session) -> Dict[str, Any]:
    """Placeholder server freshness check."""
    return {}


def compute_all_risk_tiers(session: Session) -> list:
    """Placeholder for risk tier computation."""
    return []


def get_server_scoring_trend(server_id: str, session: Session) -> Dict[str, Any]:
    """Placeholder for scoring trend."""
    return {}


def lookup_refs_by_indicator(indicator: str, session: Session) -> list:
    """Placeholder for threat intel reference lookup."""
    return []


def _query_service_health(service_name: str, session: Session) -> Dict[str, Any]:
    """Placeholder for service health query."""
    return {}


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        db = TestingSessionLocal()
        servers = [
            McpServerRegistry(server_id=f"s{i}", risk_tier="low", name=f"Server {i}", url=f"http://s{i}.example.com")
            for i in range(1, 4)
        ]
        servers[0].risk_tier = "low"
        servers[1].risk_tier = "medium"
        servers[2].risk_tier = "high"
        servers.append(
            McpServerRegistry(server_id="s4", risk_tier="critical", name="Server 4", url="http://s4.example.com")
        )
        servers.append(
            McpServerRegistry(server_id="s5", risk_tier="low", name="Server 5", url="http://s5.example.com")
        )
        db.add_all(servers)
        db.commit()
        db.close()

        response = client.get("/api/risk/snapshot")
        assert response.status_code == 200
        data = response.json()
        assert data["total_servers"] == 5
        distribution = data["distribution"]
        assert "low" in distribution
        assert "medium" in distribution
        assert "high" in distribution
        assert "critical" in distribution
        print("PASS")
