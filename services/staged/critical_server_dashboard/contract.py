# services/staged/critical_server_dashboard/contract.py
from datetime import datetime
from typing import List, Generator

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must not be changed)
from app.db import Base, get_session as app_get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class ServerInfo(BaseModel):
    server_id: str
    name: str | None
    url: str | None
    risk_tier: str | None
    overall_risk: str | None
    last_assessed: datetime | None


class DashboardResponse(BaseModel):
    servers: List[ServerInfo]


@router.get(
    "/critical-servers/dashboard",
    response_model=DashboardResponse,
    summary="Critical servers dashboard",
)
def get_critical_server_dashboard(
    session: Session = Depends(app_get_session),
):
    # Identify servers with the two high‑risk tiers
    target_tiers = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}
    servers = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(target_tiers))
        .all()
    )
    if not servers:
        raise HTTPException(status_code=404, detail="No critical servers found")

    result = [
        ServerInfo(
            server_id=s.server_id,
            name=s.name,
            url=s.url,
            risk_tier=s.risk_tier,
            overall_risk=s.risk_tier,  # placeholder – same as tier for now
            last_assessed=s.last_assessed,
        )
        for s in servers
    ]
    return DashboardResponse(servers=result)


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.critical_server_dashboard.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Dependency override
    def get_test_session() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[app_get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        now = datetime.utcnow()
        # HIGH_RISK_ISOLATED servers
        for i in range(1, 4):
            db.add(
                McpServerRegistry(
                    server_id=f"high-{i}",
                    name=f"HighRisk{i}",
                    url=f"https://high{i}.example.com",
                    risk_tier="HIGH_RISK_ISOLATED",
                    last_assessed=now,
                )
            )
        # KNOWN_THREAT servers
        known_ids = []
        for i in range(1, 3):
            sid = f"known-{i}"
            known_ids.append(sid)
            db.add(
                McpServerRegistry(
                    server_id=sid,
                    name=f"KnownThreat{i}",
                    url=f"https://known{i}.example.com",
                    risk_tier="KNOWN_THREAT",
                    last_assessed=now,
                )
            )
        db.commit()

    # ------------------------------------------------------------------- #
    # Run test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/critical-servers/dashboard")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "servers" in data, "Missing 'servers' key"
    servers = data["servers"]
    assert len(servers) == 5, f"Expected 5 servers, got {len(servers)}"
    # Verify at least one known threat server is present
    known_present = any(s["server_id"] in known_ids for s in servers)
    assert known_present, "Known threat server not found in response"

    print("PASS")