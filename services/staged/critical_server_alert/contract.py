# services/staged/critical_server_alert/contract.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry

router = APIRouter()


class ServerInfo(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_assessed: datetime


class AlertResponse(BaseModel):
    servers: List[ServerInfo]
    count: int


@router.get("/api/alert/critical", response_model=AlertResponse)
def get_critical_alert(session: Session = Depends(get_session)):
    """Return all servers whose risk tier is HIGH_RISK_ISOLATED."""
    rows = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier == "HIGH_RISK_ISOLATED")
        .order_by(McpServerRegistry.last_assessed.desc())
        .all()
    )
    servers = [
        ServerInfo(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            last_assessed=row.last_assessed,
        )
        for row in rows
    ]
    return AlertResponse(servers=servers, count=len(servers))


if __name__ == "__main__":
    # ---- Self‑test ---------------------------------------------------------
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (StaticPool) for the test session
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Seed the test database with a known critical server
    test_server = McpServerRegistry(
        server_id="srv-123",
        name="Critical Server",
        risk_tier="HIGH_RISK_ISOLATED",
        last_assessed=datetime.utcnow(),
    )
    with TestSessionLocal() as db:
        db.add(test_server)
        db.commit()

    # Dependency override to use the test session
    def get_test_session() -> Session:
        with TestSessionLocal() as db:
            yield db

    test_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(test_app)
    resp = client.get("/api/alert/critical")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["count"] == 1, f"Expected count 1, got {data['count']}"
    assert any(s["server_id"] == "srv-123" for s in data["servers"]), "Known server missing"
    print("PASS")