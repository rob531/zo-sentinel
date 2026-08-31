"""FastAPI router for McpServerRegistry CRUD operations.

Provides endpoints to list, retrieve, and create server registry entries scoped by
organization. Uses the real application DB session via ``Depends(get_session)``
so that production code shares the same data-access contract.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/servers", tags=["server_registry"])


class ServerBase(BaseModel):
    server_id: str = Field(..., description="Unique identifier for the server")
    name: str = Field(..., description="Human‑readable name")
    url: str = Field(..., description="Base URL of the server")
    trust_score: float | None = Field(None, description="Trust score from 0 to 1")
    verdict: str | None = Field(None, description="Risk verdict, e.g. LOW, HIGH")
    confidence: float | None = Field(None, description="Confidence of the verdict")
    org_id: int = Field(..., description="Owning organization ID")

    class Config:
        orm_mode = True


class ServerResponse(ServerBase):
    pass


@router.get("/", response_model=List[ServerResponse])
def list_servers(org_id: int, db: Session = Depends(get_session)) -> List[ServerResponse]:
    """Return all servers belonging to *org_id*.

    In production the *org_id* should be derived from the authenticated principal;
    the parameter is kept for simplicity in the self‑test.
    """
    servers = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.org_id == org_id)
        .all()
    )
    return [ServerResponse.from_orm(s) for s in servers]


@router.get("/{server_id}", response_model=ServerResponse)
def get_server(server_id: str, org_id: int, db: Session = Depends(get_session)) -> ServerResponse:
    """Retrieve a single server record.

    Returns 404 if the server does not belong to *org_id*.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id, McpServerRegistry.org_id == org_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return ServerResponse.from_orm(server)


@router.post("/", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
def create_server(payload: ServerBase, db: Session = Depends(get_session)) -> ServerResponse:
    """Create a new server entry.

    The combination ``(server_id, org_id)`` must be unique. If a conflict occurs a
    409 response is returned.
    """
    # Ensure no existing record with same server_id for the org
    exists = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == payload.server_id, McpServerRegistry.org_id == payload.org_id)
        .first()
    )
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Server already exists")
    new_server = McpServerRegistry(
        server_id=payload.server_id,
        name=payload.name,
        url=payload.url,
        trust_score=payload.trust_score,
        verdict=payload.verdict,
        confidence=payload.confidence,
        org_id=payload.org_id,
    )
    db.add(new_server)
    db.commit()
    db.refresh(new_server)
    return ServerResponse.from_orm(new_server)


def _run_self_test() -> None:
    """Self‑test executed when the module is run as a script.

    Spins up a temporary FastAPI app, overrides the DB dependency with an in‑memory
    SQLite session, populates a test record and exercises the three endpoints.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables using the real model metadata
    from app.models import Base

    Base.metadata.create_all(bind=engine)

    # Dependency override
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Insert a test server via the API
    payload = {
        "server_id": "srv1",
        "name": "Test Server",
        "url": "https://example.com",
        "trust_score": 0.9,
        "verdict": "LOW",
        "confidence": 0.95,
        "org_id": 42,
    }
    resp = client.post("/servers/", json=payload)
    assert resp.status_code == 201, f"create status {resp.status_code}"

    # List servers for org
    resp = client.get("/servers/?org_id=42")
    assert resp.status_code == 200, f"list status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list) and len(data) == 1, "list unexpected"

    # Get the specific server
    resp = client.get("/servers/srv1?org_id=42")
    assert resp.status_code == 200, f"get status {resp.status_code}"
    server = resp.json()
    assert server["name"] == "Test Server", "wrong server returned"

    # Verify 404 for missing server
    resp = client.get("/servers/unknown?org_id=42")
    assert resp.status_code == 404, "expected 404"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()
