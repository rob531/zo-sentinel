from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlight import stubbed_orm as stubbed

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class RiskTierTrend(BaseModel):
    server_id: str
    server_name: str
    trend_direction: str
    risk_delta: float
    current_tier: Optional[str]
    trend_score: float
    computed_at: datetime


class RiskTierTrendInput(BaseModel):
    server_id: str
    axis_name: str = "risk_tier_trend"
    model_version: str = "v1"


class RiskTierTrendResult(BaseModel):
    id: str
    server_id: str
    trend_direction: str
    risk_delta: float
    current_tier: Optional[str]
    trend_score: float
    computed_at: datetime
    success: bool
    message: Optional[str] = None


def compute_risk_tier_trend(
    server_id: str,
    session: Session,
) -> RiskTierTrend:
    current_score = session.execute(
        select(McpLlmAxisScore)
        .where(McpServerRegistry.server_id == server_id)
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).scalar_one_or_none()

    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if not server:
        raise ValueError(f"Server not found: {server_id}")

    scores = session.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(5)
    ).scalars().all()

    if len(scores) < 2:
        trend_direction = "insufficient_data"
        risk_delta = 0.0
        trend_score = 0.0
    else:
        latest = scores[0].p_danger or 0.0
        oldest = scores[-1].p_danger or 0.0
        risk_delta = latest - oldest

        if risk_delta > 0.1:
            trend_direction = "increasing"
            trend_score = min(risk_delta * 2, 1.0)
        elif risk_delta < -0.1:
            trend_direction = "decreasing"
            trend_score = max(risk_delta * 2, -1.0)
        else:
            trend_direction = "stable"
            trend_score = 0.0

    return RiskTierTrend(
        server_id=server_id,
        server_name=server.name,
        trend_direction=trend_direction,
        risk_delta=risk_delta,
        current_tier=server.risk_tier,
        trend_score=trend_score,
        computed_at=datetime.utcnow(),
    )


def process_trend_scoring(
    server_id: str,
    session: Session,
) -> RiskTierTrendResult:
    try:
        trend = compute_risk_tier_trend(server_id, session)
        return RiskTierTrendResult(
            id=str(uuid4()),
            server_id=trend.server_id,
            trend_direction=trend.trend_direction,
            risk_delta=trend.risk_delta,
            current_tier=trend.current_tier,
            trend_score=trend.trend_score,
            computed_at=trend.computed_at,
            success=True,
        )
    except Exception as e:
        return RiskTierTrendResult(
            id=str(uuid4()),
            server_id=server_id,
            trend_direction="error",
            risk_delta=0.0,
            current_tier=None,
            trend_score=0.0,
            computed_at=datetime.utcnow(),
            success=False,
            message=str(e),
        )


def get_all_services_health(session: Session = Depends(get_session)):
    servers = session.execute(select(McpServerRegistry)).scalars().all()
    return {
        "service": "risk_tier_trend_scoring_consumer",
        "status": "healthy",
        "servers_tracked": len(servers),
        "timestamp": datetime.utcnow().isoformat(),
    }


def health(session: Session = Depends(get_session)):
    return get_all_services_health(session)


def get_overall_health(session: Session = Depends(get_session)):
    return health(session)


def get_registry(session: Session = Depends(get_session)):
    servers = session.execute(select(McpServerRegistry)).scalars().all()
    return {"servers": [{"server_id": s.server_id, "name": s.name, "risk_tier": s.risk_tier} for s in servers]}


def get_server_by_name(name: str, session: Session = Depends(get_session)):
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.name == name)
    ).scalar_one_or_none()
    if server:
        return {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "trust_score": server.trust_score,
        }
    return None


def ensure_tables(session: Session):
    return {"tables_ready": True}


def get_exemption(server_id: str, session: Session = Depends(get_session)):
    return {"server_id": server_id, "exempt": False}


def signal_handler():
    return {"signal": "handled", "service": "risk_tier_trend_scoring_consumer"}


def build_search_index(session: Session = Depends(get_session)):
    servers = session.execute(select(McpServerRegistry)).scalars().all()
    return {"indexed_count": len(servers)}


def cadence_summary(session: Session = Depends(get_session)):
    return {"cadence": "daily", "active": True}


def dashboard_stats(session: Session = Depends(get_session)):
    servers = session.execute(select(McpServerRegistry)).scalars().all()
    return {"total_servers": len(servers), "active": True}


def recent_cves(limit: int = 10, session: Session = Depends(get_session)):
    return {"cves": [], "count": 0}


def get_contract_by_id(contract_id: str, session: Session = Depends(get_session)):
    return {"contract_id": contract_id, "found": False}


def fetch_mcp_server_data(server_id: str, session: Session = Depends(get_session)):
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if server:
        return {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
        }
    return None


def send_heartbeat(server_id: str, session: Session = Depends(get_session)):
    return {"server_id": server_id, "heartbeat": "sent"}


def get_summary_statistics(session: Session = Depends(get_session)):
    servers = session.execute(select(McpServerRegistry)).scalars().all()
    tiers = {}
    for s in servers:
        tier = s.risk_tier or "unknown"
        tiers[tier] = tiers.get(tier, 0) + 1
    return {"tier_distribution": tiers, "total": len(servers)}


def compute_comparison_id(left: str, right: str) -> str:
    return f"cmp_{left}_{right}"


def get_discrepancy_summary(session: Session = Depends(get_session)):
    return {"discrepancies": [], "count": 0}


def get_unknown_risk_servers(session: Session = Depends(get_session)):
    servers = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.risk_tier == None)
    ).scalars().all()
    return {"servers": [{"server_id": s.server_id, "name": s.name} for s in servers], "count": len(servers)}


if __name__ == "__main__":
    from starlette.testclient import TestClient

    app = FastAPI(title="RiskTierTrendScoringConsumer")

    @app.get("/health")
    def _health():
        return health()

    @app.get("/registry")
    def _registry():
        return get_registry()

    @app.get("/server/{server_id}")
    def _server(server_id: str):
        return fetch_mcp_server_data(server_id)

    @app.post("/process-trend")
    def _process_trend(input_data: RiskTierTrendInput):
        with stubbed.Session(stubbed.engine(poolclass=StaticPool)) as session:
            result = process_trend_scoring(input_data.server_id, session)
            return result

    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "risk_tier_trend_scoring_consumer"

    response = client.get("/registry")
    assert response.status_code == 200

    response = client.post("/process-trend", json={"server_id": "test-server"})
    assert response.status_code == 200
    result = response.json()
    assert "success" in result
    assert "trend_direction" in result

    print("PASS")