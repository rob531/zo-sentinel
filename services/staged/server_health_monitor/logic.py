from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_health_monitor"])

STALE_THRESHOLD_MINUTES = 30
ORPHANED_THRESHOLD_MINUTES = 60 * 24


def compute_health_status(last_seen: Optional[datetime], last_scanned: Optional[datetime]) -> str:
    if last_seen is None:
        return "orphaned"
    now = datetime.utcnow()
    age_minutes = (now - last_seen).total_seconds() / 60
    if age_minutes > ORPHANED_THRESHOLD_MINUTES:
        return "orphaned"
    if age_minutes > STALE_THRESHOLD_MINUTES:
        return "stale"
    return "active"


class ServerHealthResponse(BaseModel):
    server_id: str
    last_seen: Optional[datetime]
    last_scanned: Optional[datetime]
    status: str


@router.get("/servers/{server_id}/health", response_model=ServerHealthResponse)
def get_server_health(
    server_id: str,
    session: Session = Depends(get_session)
) -> ServerHealthResponse:
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    
    return ServerHealthResponse(
        server_id=server.server_id,
        last_seen=server.last_seen,
        last_scanned=server.last_scanned,
        status=compute_health_status(server.last_seen, server.last_scanned)
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    McpServerRegistry.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)
    
    def get_session_override() -> Session:
        return TestSessionLocal()
    
    with get_session_override() as session:
        session.add(McpServerRegistry(
            server_id="server-active",
            name="Active Server",
            last_seen=datetime.utcnow() - timedelta(minutes=5),
            last_scanned=datetime.utcnow() - timedelta(minutes=10),
            url="http://active.example.com",
            registry_source="test"
        ))
        session.add(McpServerRegistry(
            server_id="server-stale",
            name="Stale Server",
            last_seen=datetime.utcnow() - timedelta(hours=1),
            last_scanned=datetime.utcnow() - timedelta(hours=2),
            url="http://stale.example.com",
            registry_source="test"
        ))
        session.add(McpServerRegistry(
            server_id="server-orphaned",
            name="Orphaned Server",
            last_seen=datetime.utcnow() - timedelta(days=2),
            last_scanned=datetime.utcnow() - timedelta(days=3),
            url="http://orphaned.example.com",
            registry_source="test"
        ))
        session.commit()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    
    assert client.get("/api/servers/server-active/health").json()["status"] == "active"
    assert client.get("/api/servers/server-stale/health").json()["status"] == "stale"
    assert client.get("/api/servers/server-orphaned/health").json()["status"] == "orphaned"
    assert client.get("/api/servers/nonexistent/health").status_code == 404
    
    print("PASS")