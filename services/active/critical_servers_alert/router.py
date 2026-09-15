# deps: requests,fastapi
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["critical_servers_alert"])


# ---------- Pydantic response models ----------
class EscalatedAxis(BaseModel):
    axis_name: str
    label: str
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated_to: Optional[str] = None


class ServerAlert(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_assessed: Optional[datetime] = None
    escalated_axes: list[EscalatedAxis] = []


class CriticalServersResponse(BaseModel):
    servers: list[ServerAlert] = []


# ---------- Endpoint ----------
@router.get(
    "/alerts/critical-servers",
    response_model=CriticalServersResponse,
)
def get_critical_servers_alert(
    risk_tier: Optional[str] = Query(
        None,
        description="Filter by specific risk tier (e.g. HIGH_RISK_ISOLATED, KNOWN_THREAT)",
    ),
    session: Session = Depends(get_session),
) -> CriticalServersResponse:
    """
    Return servers in HIGH_RISK_ISOLATED or KNOWN_THREAT risk tiers
    (or the specified tier), together with any escalated LLM axis scores.
    """
    risk_tiers = (risk_tier,) if risk_tier else ("HIGH_RISK_ISOLATED", "KNOWN_THREAT")
    servers = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(risk_tiers))
        .all()
    )

    result_servers: list[ServerAlert] = []
    for srv in servers:
        axes = (
            session.query(McpLlmAxisScore)
            .filter(
                McpLlmAxisScore.server_id == srv.server_id,
                McpLlmAxisScore.escalated.is_(True),
            )
            .all()
        )
        escalated_list = [
            EscalatedAxis(
                axis_name=ax.axis_name,
                label=ax.label,
                p_critical=float(ax.p_critical) if ax.p_critical is not None else None,
                p_danger=float(ax.p_danger) if ax.p_danger is not None else None,
                escalated_to=ax.escalated_to,
            )
            for ax in axes
        ]
        result_servers.append(
            ServerAlert(
                server_id=srv.server_id,
                name=srv.name,
                risk_tier=srv.risk_tier,
                last_assessed=srv.last_assessed,
                escalated_axes=escalated_list,
            )
        )

    return CriticalServersResponse(servers=result_servers)


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(test_engine)
    McpLlmAxisScore.metadata.create_all(test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    with TestSessionLocal() as db:
        srv1 = McpServerRegistry(
            server_id="srv-001",
            name="Critical Server 1",
            risk_tier="HIGH_RISK_ISOLATED",
            last_assessed=datetime.utcnow(),
        )
        srv2 = McpServerRegistry(
            server_id="srv-002",
            name="Critical Server 2",
            risk_tier="KNOWN_THREAT",
            last_assessed=datetime.utcnow(),
        )
        srv3 = McpServerRegistry(
            server_id="srv-003",
            name="Normal Server",
            risk_tier="LOW_RISK",
            last_assessed=datetime.utcnow(),
        )
        db.add_all([srv1, srv2, srv3])
        ax = McpLlmAxisScore(
            server_id="srv-001",
            axis_name="malware",
            label="Malware Detected",
            escalated=True,
            escalated_to="SOC",
            p_critical=0.92,
            p_danger=0.85,
        )
        db.add(ax)
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(test_app)

    resp = client.get("/api/alerts/critical-servers")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    payload = resp.json()
    servers = payload.get("servers", [])
    if len(servers) < 1:
        print("FAIL: no servers returned", file=sys.stderr)
        sys.exit(1)
    flagged = next((s for s in servers if s["server_id"] == "srv-001"), None)
    if flagged is None:
        print("FAIL: flagged server missing", file=sys.stderr)
        sys.exit(1)
    if not flagged.get("escalated_axes"):
        print("FAIL: escalated axes missing for flagged server", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)
