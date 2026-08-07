# deps: fastapi, sqlalchemy, pydantic
"""MCP Server Details Service.

Provides basic information about MCP servers stored in the core `mcp_server_registry`
application table. Public endpoints – no authentication required.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_server_details"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class ServerDetail(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    url: Optional[str]
    description: Optional[str]
    trust_score: Optional[float]
    verdict: Optional[str]
    confidence: Optional[float]
    last_assessed: Optional[datetime]

    class Config:
        orm_mode = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/servers", response_model=List[ServerDetail])
def list_servers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> List[ServerDetail]:
    """Return a paginated list of MCP servers.

    The underlying query is a simple SELECT on the `mcp_server_registry` table.
    """
    servers = (
        db.query(McpServerRegistry)
        .order_by(McpServerRegistry.server_id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [ServerDetail.from_orm(s) for s in servers]


@router.get("/servers/{server_id}", response_model=ServerDetail)
def get_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerDetail:
    """Return details for a single server identified by ``server_id``.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return ServerDetail.from_orm(server)


# ---------------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.models import Base

    # In‑memory SQLite for isolated testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Dependency override to provide the test session
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Populate test data
    with SessionLocal() as db:
        db.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, registry_source, url, description, trust_score, verdict, confidence)
                VALUES
                    ('srv-1', 'Server One', 'github', 'https://github.com/srv1', 'Test server 1', 0.85, 'HIGH', 0.9),
                    ('srv-2', 'Server Two', 'npm', 'https://npmjs.com/srv2', NULL, 0.45, 'LOW', 0.7);
                """
            )
        )
        db.commit()

    # 1. List endpoint
    resp = client.get("/api/servers?skip=0&limit=10")
    assert resp.status_code == 200, f"List endpoint failed: {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list) and len(data) == 2, "Expected two servers"

    # 2. Detail endpoint (existing)
    resp = client.get("/api/servers/srv-1")
    assert resp.status_code == 200, "Detail endpoint failed for existing server"
    detail = resp.json()
    assert detail["server_id"] == "srv-1"
    assert detail["name"] == "Server One"

    # 3. Detail endpoint (non‑existent)
    resp = client.get("/api/servers/unknown")
    assert resp.status_code == 404, "Expected 404 for unknown server"

    print("PASS")
    sys.exit(0)
