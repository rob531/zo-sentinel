from datetime import datetime, timedelta
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])


class ServerSummary(BaseModel):
    server_id: str
    name: str
    last_assessed: datetime
    risk_tier: Optional[str] = None
    scan_count: Optional[int] = None


class StaleServersResponse(BaseModel):
    total: int
    servers: list[ServerSummary]


@router.get("/servers/stale", response_model=StaleServersResponse)
async def get_stale_servers(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> StaleServersResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        select(McpServerRegistry)
        .where(McpServerRegistry.last_assessed < cutoff)
        .order_by(McpServerRegistry.last_assessed.asc())
    )
    result = session.execute(query)
    rows = result.scalars().all()

    servers = [
        ServerSummary(
            server_id=row.server_id,
            name=row.name,
            last_assessed=row.last_assessed,
            risk_tier=row.risk_tier,
            scan_count=row.scan_count,
        )
        for row in rows
    ]

    return StaleServersResponse(total=len(servers), servers=servers)


def run():
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime.utcnow()
    servers = [
        McpServerRegistry(
            server_id="srv-001",
            name="Server Alpha",
            last_assessed=now - timedelta(days=45),
            risk_tier="high",
            scan_count=10,
            url="http://alpha.local",
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="Server Beta",
            last_assessed=now - timedelta(days=15),
            risk_tier="medium",
            scan_count=5,
            url="http://beta.local",
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="Server Gamma",
            last_assessed=now - timedelta(days=60),
            risk_tier="critical",
            scan_count=2,
            url="http://gamma.local",
            registry_source="test",
        ),
    ]
    for s in servers:
        session.add(s)
    session.commit()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    that_app = app
    that_app.dependency_overrides[get_session] = lambda: session
    client = TestClient(that_app)

    response = client.get("/api/servers/stale?days=30")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "servers" in data, f"Expected servers in response, got {data}"
    assert len(data["servers"]) == 2, f"Expected 2 servers, got {len(data['servers'])}"
    assert data["servers"][0]["server_id"] == "srv-003", "Expected srv-003 first (oldest)"
    assert data["servers"][1]["server_id"] == "srv-001", "Expected srv-001 second"
    print("PASS")


if __name__ == "__main__":
    run()