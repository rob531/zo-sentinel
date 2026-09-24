from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Dict, Tuple, Optional
from datetime import datetime
import asyncio

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from app.main import app
from write_service import write_service

router = APIRouter()


class ServerStatus(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    tier_source: str
    scored_at: Optional[datetime]


class ServerStatusResponse(BaseModel):
    servers: List[ServerStatus]


def compute_risk_tier(db: Session, server_id: str) -> Tuple[str, str]:
    """Compute live risk_tier for a server based on 6 axes and trust_gate override."""
    trust_gate_override = None
    try:
        trust_records = write_service(
            "mcp_risk_register",
            {"filter": {"server_id": server_id}, "limit": 1}
        )
        if trust_records and len(trust_records) > 0:
            trust_gate_override = trust_records[0].get("trust_gating_override", {}).get("trust_gate")
    except Exception:
        pass

    if trust_gate_override == "BLOCK" or trust_gate_override == "CRITICAL":
        return "CRITICAL", "trust_gate_override"

    scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    return _compute_tier_from_scores(scores)


def _compute_tier_from_scores(scores: List[McpLlmAxisScore]) -> Tuple[str, str]:
    """Derive risk_tier from 6 axis scores."""
    axes_scores = {}
    for score in scores:
        if score.axis_name in [
            "authority_jailbreak", "tool_injection", "data_exfiltration",
            "harmful_content", "system_prompt_extraction", "policy_violation"
        ]:
            axes_scores[score.axis_name] = float(score.p_danger or 0)
            if score.escalated:
                return "CRITICAL", "axis_escalation"

    if not axes_scores:
        return "MINIMAL", "no_scores"

    max_p_danger = max(axes_scores.values()) if axes_scores else 0
    if max_p_danger >= 0.7:
        return "HIGH", "axis_aggregation"
    elif max_p_danger >= 0.4:
        return "MEDIUM", "axis_aggregation"
    elif max_p_danger >= 0.15:
        return "LOW", "axis_aggregation"
    else:
        return "MINIMAL", "axis_aggregation"


def get_axis_scores_for_server(db: Session, server_id: str) -> List[McpLlmAxisScore]:
    """Get all axis scores for a specific server."""
    return db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).order_by(McpLlmAxisScore.scored_at.desc()).all()


@router.get("/api/scoring/consumer/status", response_model=ServerStatusResponse)
async def get_scoring_status(db: Session = Depends(get_session)):
    """Get current scoring status for all servers with their computed risk tiers."""
    subquery = db.query(
        McpLlmAxisScore.server_id,
        func.max(McpLlmAxisScore.scored_at).label("max_scored_at")
    ).group_by(McpLlmAxisScore.server_id).subquery()

    servers_with_scores = db.query(
        McpServerRegistry, McpLlmAxisScore
    ).join(
        subquery, McpServerRegistry.server_id == subquery.c.server_id
    ).join(
        McpLlmAxisScore,
        (McpLlmAxisScore.server_id == subquery.c.server_id) &
        (McpLlmAxisScore.scored_at == subquery.c.max_scored_at)
    ).all()

    servers = []
    for server, latest_score in servers_with_scores:
        tier, source = compute_risk_tier(db, server.server_id)
        servers.append(ServerStatus(
            server_id=server.server_id,
            name=server.name,
            risk_tier=tier,
            tier_source=source,
            scored_at=latest_score.scored_at if latest_score else None
        ))

    return ServerStatusResponse(servers=servers)


async def _heartbeat():
    """Send heartbeat to service_health every 60 seconds."""
    while True:
        try:
            write_service(
                "service_health",
                {"service": "scoring_consumer_tier", "status": "healthy", "timestamp": datetime.utcnow().isoformat()}
            )
        except Exception:
            pass
        await asyncio.sleep(60)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)

    with test_app.test_client() as client:
        db = TestingSessionLocal()

        s1 = McpServerRegistry(server_id="srv1", name="Server Alpha", risk_tier="LOW")
        s2 = McpServerRegistry(server_id="srv2", name="Server Beta", risk_tier="LOW")
        s3 = McpServerRegistry(server_id="srv3", name="Server Gamma", risk_tier="LOW")
        db.add_all([s1, s2, s3])

        now = datetime.utcnow()
        db.add(McpLlmAxisScore(server_id="srv1", axis_name="authority_jailbreak", p_danger=0.05, scored_at=now))
        db.add(McpLlmAxisScore(server_id="srv1", axis_name="tool_injection", p_danger=0.08, scored_at=now))
        db.add(McpLlmAxisScore(server_id="srv2", axis_name="authority_jailbreak", p_danger=0.75, scored_at=now))
        db.add(McpLlmAxisScore(server_id="srv2", axis_name="data_exfiltration", p_danger=0.80, scored_at=now))
        db.add(McpLlmAxisScore(server_id="srv3", axis_name="policy_violation", p_danger=0.90, scored_at=now, escalated=True))
        db.commit()

        app.dependency_overrides[get_session] = override_get_session

        response = client.get("/api/scoring/consumer/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        valid_tiers = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "UNKNOWN"}
        for server in data.get("servers", []):
            assert server["risk_tier"] in valid_tiers, f"Invalid tier: {server['risk_tier']}"

        app.dependency_overrides.clear()
        db.close()
        print("PASS")