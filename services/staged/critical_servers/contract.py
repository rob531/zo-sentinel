"""Critical servers contract.

Provides a FastAPI router exposing:
GET /api/servers/critical
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class ServerInfo(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_assessed: datetime | None


class ServersResponse(BaseModel):
    servers: List[ServerInfo]


@router.get(
    "/servers/critical",
    response_model=ServersResponse,
    status_code=status.HTTP_200_OK,
    summary="List critical servers",
)
def get_critical_servers(db: Session = Depends(get_session)):
    """Return servers whose risk tier is HIGH_RISK_ISOLATED or KNOWN_THREAT."""
    stmt = select(McpServerRegistry).where(
        McpServerRegistry.risk_tier.in_(["HIGH_RISK_ISOLATED", "KNOWN_THREAT"])
    )
    results = db.execute(stmt).scalars().all()
    servers = [
        ServerInfo(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            last_assessed=row.last_assessed,
        )
        for row in results
    ]
    return ServersResponse(servers=servers)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.critical_servers.contract`) #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite app for the test
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Seed two critical servers
    with TestSessionLocal() as sess:
        sess.add_all(
            [
                McpServerRegistry(
                    server_id="srv-001",
                    name="Critical Server One",
                    risk_tier="HIGH_RISK_ISOLATED",
                    last_assessed=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="srv-002",
                    name="Critical Server Two",
                    risk_tier="KNOWN_THREAT",
                    last_assessed=datetime.utcnow(),
                ),
            ]
        )
        sess.commit()

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        with TestSessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Run test client
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/servers/critical")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict) and "servers" in data, "Missing 'servers' key"
    assert len(data["servers"]) == 2, f"Expected 2 servers, got {len(data['servers'])}"
    print("PASS")