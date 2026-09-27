from datetime import datetime
from typing import Any, Dict
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class DashboardSummaryResponse(BaseModel):
    total_servers: int
    healthy_servers: int
    stale_servers: int
    risk_tier_breakdown: Dict[str, int]
    scored_servers: int
    average_confidence: float
    last_updated: str | None = None


def compute_dashboard_summary(db: Session) -> Dict[str, Any]:
    """Compute dashboard summary from McpServerRegistry and McpLlmAxisScore."""
    server_stats = db.execute(
        select(
            func.count(McpServerRegistry.id).label("total_servers"),
            func.sum(
                func.cast(
                    McpServerRegistry.status == "healthy",
                    server_stats.type.python_type
                )
            ).label("healthy_servers"),
            func.sum(
                func.cast(
                    McpServerRegistry.status == "stale",
                    server_stats.type.python_type
                )
            ).label("stale_servers"),
        )
    ).one()

    total_servers = server_stats.total_servers or 0
    healthy_servers = server_stats.healthy_servers or 0
    stale_servers = server_stats.stale_servers or 0

    risk_tier_breakdown_query = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.id)
        ).group_by(McpServerRegistry.risk_tier)
    ).all()

    risk_tier_breakdown = {
        row.risk_tier: row.count for row in risk_tier_breakdown_query
    }

    score_stats = db.execute(
        select(
            func.count(McpLlmAxisScore.id).label("scored_servers"),
            func.avg(McpLlmAxisScore.confidence).label("average_confidence"),
        )
    ).one()

    scored_servers = score_stats.scored_servers or 0
    average_confidence = score_stats.average_confidence or 0.0

    last_updated = datetime.utcnow().isoformat()

    return DashboardSummaryResponse(
        total_servers=total_servers,
        healthy_servers=healthy_servers,
        stale_servers=stale_servers,
        risk_tier_breakdown=risk_tier_breakdown,
        scored_servers=scored_servers,
        average_confidence=float(average_confidence),
        last_updated=last_updated,
    ).model_dump()


@router.get("/api/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Get dashboard summary endpoint."""
    return compute_dashboard_summary(db)


if __name__ == "__main__":
    from app.models import Base
    from fastapi.testclient import TestClient
    from main import app

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    servers_data = [
        {"name": "server1", "status": "healthy", "risk_tier": "low", "endpoint": "http://a"},
        {"name": "server2", "status": "healthy", "risk_tier": "low", "endpoint": "http://b"},
        {"name": "server3", "status": "healthy", "risk_tier": "low", "endpoint": "http://c"},
        {"name": "server4", "status": "healthy", "risk_tier": "medium", "endpoint": "http://d"},
        {"name": "server5", "status": "healthy", "risk_tier": "medium", "endpoint": "http://e"},
        {"name": "server6", "status": "healthy", "risk_tier": "medium", "endpoint": "http://f"},
        {"name": "server7", "status": "stale", "risk_tier": "high", "endpoint": "http://g"},
        {"name": "server8", "status": "stale", "risk_tier": "high", "endpoint": "http://h"},
        {"name": "server9", "status": "stale", "risk_tier": "high", "endpoint": "http://i"},
        {"name": "server10", "status": "stale", "risk_tier": "high", "endpoint": "http://j"},
    ]

    for s in servers_data:
        srv = McpServerRegistry(name=s["name"], status=s["status"], risk_tier=s["risk_tier"], endpoint=s["endpoint"])
        db.add(srv)
    db.commit()

    for i, srv in enumerate(db.query(McpServerRegistry).all()[:5]):
        score = McpLlmAxisScore(server_id=srv.id, axis_name=f"axis_{i}", confidence=0.5 + i * 0.1)
        db.add(score)
    db.commit()

    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)

    response = client.get("/api/dashboard/summary")
    data = response.json()

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert data["total_servers"] == 10, f"Expected total_servers=10, got {data['total_servers']}"
    assert len(data["risk_tier_breakdown"]) == 3, f"Expected 3 risk tiers, got {len(data['risk_tier_breakdown'])}"

    print("PASS")

    db.close()