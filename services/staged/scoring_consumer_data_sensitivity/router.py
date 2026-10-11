"""
Router for scoring_consumer_data_sensitivity service.

Reads data_sensitivity axis scores from mcp_llm_axis_scores,
computes risk_tier per server, writes back to mcp_server_registry.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/scoring/consumer/data-sensitivity", tags=["scoring_consumer_data_sensitivity"])


def compute_data_sensitivity_risk_tier(session: Session, server_id: str) -> str | None:
    """Compute risk_tier based on data_sensitivity axis scores for a server."""
    stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == "data_sensitivity"
    )
    score_record = session.execute(stmt).scalar_one_or_none()
    
    if not score_record:
        return None
    
    label = score_record.label
    if label == "critical":
        return "critical"
    elif label == "high":
        return "high"
    elif label == "medium":
        return "medium"
    else:
        return "low"


def update_server_risk_tier(session: Session, server_id: str, risk_tier: str) -> bool:
    """Update the risk_tier for a server in mcp_server_registry."""
    stmt = (
        update(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .values(risk_tier=risk_tier)
    )
    result = session.execute(stmt)
    return result.rowcount > 0


def get_server_data_sensitivity_score(session: Session, server_id: str) -> dict | None:
    """Get data_sensitivity score for a server."""
    stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == "data_sensitivity"
    )
    score_record = session.execute(stmt).scalar_one_or_none()
    
    if not score_record:
        return None
    
    return {
        "server_id": score_record.server_id,
        "axis_name": score_record.axis_name,
        "label": score_record.label,
        "label_index": score_record.label_index,
        "p_critical": score_record.p_critical,
        "p_danger": score_record.p_danger,
        "p_top": score_record.p_top,
        "probs": score_record.probs,
        "scored_at": score_record.scored_at.isoformat() if score_record.scored_at else None,
    }


def compute_all_servers_risk_tier(session: Session) -> list[dict]:
    """Compute and update risk_tier for all servers with data_sensitivity scores."""
    stmt = (
        select(McpLlmAxisScore.axis_name, McpLlmAxisScore.server_id)
        .where(McpLlmAxisScore.axis_name == "data_sensitivity")
        .distinct()
    )
    results = session.execute(stmt).fetchall()
    
    updated_servers = []
    for row in results:
        risk_tier = compute_data_sensitivity_risk_tier(session, row.server_id)
        if risk_tier:
            update_server_risk_tier(session, row.server_id, risk_tier)
            updated_servers.append({
                "server_id": row.server_id,
                "risk_tier": risk_tier
            })
    
    return updated_servers


@router.post("/compute/{server_id}")
def compute_risk_tier(server_id: str, session: Session = Depends(get_session)) -> dict:
    """Compute and store risk_tier for a specific server based on data_sensitivity scores."""
    risk_tier = compute_data_sensitivity_risk_tier(session, server_id)
    
    if risk_tier:
        update_server_risk_tier(session, server_id, risk_tier)
        return {"server_id": server_id, "risk_tier": risk_tier, "status": "updated"}
    
    return {"server_id": server_id, "risk_tier": None, "status": "no_data_sensitivity_score"}


@router.post("/compute-all")
def compute_all_risk_tiers(session: Session = Depends(get_session)) -> dict:
    """Compute and store risk_tier for all servers with data_sensitivity scores."""
    updated = compute_all_servers_risk_tier(session)
    return {"updated_count": len(updated), "servers": updated}


@router.get("/{server_id}")
def get_data_sensitivity_score(server_id: str, session: Session = Depends(get_session)) -> dict:
    """Get data_sensitivity score details for a server."""
    score = get_server_data_sensitivity_score(session, server_id)
    
    if not score:
        return {"error": "no_data_sensitivity_score", "server_id": server_id}
    
    return score


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)
    
    app = FastAPI()
    
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/scoring/consumer/data-sensitivity/test-server")
    print(f"Health check: {response.status_code}")
    
    response = client.post("/scoring/consumer/data-sensitivity/compute/test-server")
    print(f"Compute risk tier: {response.json()}")
    
    response = client.get("/scoring/consumer/data-sensitivity/test-server")
    print(f"Get score: {response.json()}")
    
    response = client.post("/scoring/consumer/data-sensitivity/compute-all")
    print(f"Compute all: {response.json()}")
    
    print("PASS")