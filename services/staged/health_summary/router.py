from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api", tags=["health_summary"])


class HealthSummaryResponse(BaseModel):
    total_servers: int
    by_tier: dict[str, int]
    average_axis_scores: dict[str, float]
    freshness: dict[str, int]
    as_of: str


def get_health_summary(session: Session) -> HealthSummaryResponse:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # Total servers
    total_result = session.execute(text("SELECT COUNT(*) FROM mcp_server_registry"))
    total_servers = total_result.scalar() or 0

    # By tier
    tier_result = session.execute(text("""
        SELECT risk_tier, COUNT(*) 
        FROM mcp_server_registry 
        GROUP BY risk_tier
    """))
    by_tier = {row[0]: row[1] for row in tier_result.fetchall() if row[0]}

    # Average axis scores
    axis_result = session.execute(text("""
        SELECT axis_name, AVG(p_danger) as avg_p_danger
        FROM mcp_llm_axis_scores
        GROUP BY axis_name
    """))
    average_axis_scores = {row[0]: round(float(row[1] or 0.0), 4) for row in axis_result.fetchall()}

    # Freshness: servers scored in last 24h
    scored_24h_result = session.execute(text("""
        SELECT COUNT(DISTINCT server_id) 
        FROM mcp_llm_axis_scores 
        WHERE scored_at >= :cutoff
    """), {"cutoff": cutoff_24h})
    scored_24h = scored_24h_result.scalar() or 0

    # Unscored servers
    unscored_result = session.execute(text("""
        SELECT COUNT(*) 
        FROM mcp_server_registry 
        WHERE server_id NOT IN (SELECT DISTINCT server_id FROM mcp_llm_axis_scores)
    """))
    unscored = unscored_result.scalar() or 0

    return HealthSummaryResponse(
        total_servers=total_servers,
        by_tier=by_tier,
        average_axis_scores=average_axis_scores,
        freshness={"scored_24h": scored_24h, "unscored": unscored},
        as_of=now.isoformat()
    )


@router.get("/summary", response_model=HealthSummaryResponse)
def get_summary(session: Session = Depends(get_session)) -> HealthSummaryResponse:
    return get_health_summary(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    import in_memory_store as store

    app = FastAPI()
    app.include_router(router)

    # Seed 5 servers with mixed risk_tiers
    seed_servers = [
        {"server_id": "srv_001", "name": "Alpha", "risk_tier": "TRUSTED_GENERAL", "registry_source": "manual"},
        {"server_id": "srv_002", "name": "Beta", "risk_tier": "TRUSTED_RESEARCH", "registry_source": "manual"},
        {"server_id": "srv_003", "name": "Gamma", "risk_tier": "EXTERNAL_UNVERIFIED", "registry_source": "manual"},
        {"server_id": "srv_004", "name": "Delta", "risk_tier": "TRUSTED_GENERAL", "registry_source": "manual"},
        {"server_id": "srv_005", "name": "Epsilon", "risk_tier": "EXTERNAL_UNVERIFIED", "registry_source": "manual"},
    ]
    for s in seed_servers:
        store.server_registry[s["server_id"]] = s

    # Seed 7 axis scores
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    seed_scores = [
        {"id": 1, "server_id": "srv_001", "axis_name": "overall_risk", "p_danger": 0.1, "scored_at": now - timedelta(hours=2)},
        {"id": 2, "server_id": "srv_001", "axis_name": "auth_strength", "p_danger": 0.05, "scored_at": now - timedelta(hours=2)},
        {"id": 3, "server_id": "srv_002", "axis_name": "overall_risk", "p_danger": 0.2, "scored_at": now - timedelta(hours=12)},
        {"id": 4, "server_id": "srv_003", "axis_name": "overall_risk", "p_danger": 0.8, "scored_at": now - timedelta(hours=30)},
        {"id": 5, "server_id": "srv_004", "axis_name": "overall_risk", "p_danger": 0.15, "scored_at": now - timedelta(hours=6)},
        {"id": 6, "server_id": "srv_004", "axis_name": "data_exposure", "p_danger": 0.25, "scored_at": now - timedelta(hours=6)},
        {"id": 7, "server_id": "srv_005", "axis_name": "overall_risk", "p_danger": 0.9, "scored_at": now - timedelta(hours=48)},
    ]
    for s in seed_scores:
        store.axis_scores.append(s)

    with TestClient(app) as client:
        response = client.get("/api/summary")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["total_servers"] >= 5, f"Expected total_servers >= 5, got {data['total_servers']}"
        assert "TRUSTED_GENERAL" in data["by_tier"], f"Expected TRUSTED_GENERAL in by_tier keys"
        assert "TRUSTED_RESEARCH" in data["by_tier"], f"Expected TRUSTED_RESEARCH in by_tier keys"
        assert "EXTERNAL_UNVERIFIED" in data["by_tier"], f"Expected EXTERNAL_UNVERIFIED in by_tier keys"
        print("PASS")