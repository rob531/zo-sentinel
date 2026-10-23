"""services.staged.server_directory.contract

FastAPI contract for the Server Directory service.
Provides a paginated, optionally filtered view of the
`mcp_server_registry` table ordered by `trust_score` descending.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class ServerInfo(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str = Field(..., description="Human‑readable name")
    registry_source: str = Field(..., description="Source of the registry entry")
    trust_score: float = Field(..., description="Trust score")
    verdict: str = Field(..., description="Verdict")
    risk_tier: str = Field(..., description="Risk tier")
    last_seen: datetime = Field(..., description="Timestamp of last sighting")


class DirectoryResponse(BaseModel):
    servers: List[ServerInfo] = Field(..., description="List of servers")
    total: int = Field(..., description="Total number of matching servers")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Number of items per page")


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/directory",
    response_model=DirectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="List servers in the directory",
)
def list_directory(
    page: int = Query(1, ge=1, description="Page number (1‑based)"),
    per_page: int = Query(
        50,
        ge=1,
        le=200,
        description="Items per page (max 200)",
    ),
    source: Optional[str] = Query(
        None,
        description="Filter by registry_source",
    ),
    db: Session = Depends(get_session),
) -> DirectoryResponse:
    """Return a paginated list of servers ordered by trust_score descending."""
    query = db.query(McpServerRegistry)

    if source is not None:
        query = query.filter(McpServerRegistry.registry_source == source)

    total = query.count()

    query = (
        query.order_by(McpServerRegistry.trust_score.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    rows = query.all()

    servers = [
        ServerInfo(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            trust_score=row.trust_score,
            verdict=row.verdict,
            risk_tier=row.risk_tier,
            last_seen=row.last_seen,
        )
        for row in rows
    ]

    return DirectoryResponse(
        servers=servers,
        total=total,
        page=page,
        per_page=per_page,
    )


def get_router() -> APIRouter:
    """Expose the router for inclusion in the main FastAPI app."""
    return router


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.server_directory.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.include_router(router)

    # SQLite in‑memory engine (StaticPool keeps the same connection)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables for the models we use
    McpServerRegistry.metadata.create_all(engine)

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the real DB dependency with the in‑memory one
    test_app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed the in‑memory DB with deterministic test data
    # ------------------------------------------------------------------- #
    seed_data = [
        {
            "server_id": "srv-001",
            "name": "Alpha",
            "registry_source": "source-a",
            "trust_score": 95.0,
            "verdict": "good",
            "risk_tier": "low",
            "last_seen": datetime(2024, 1, 1, 12, 0, 0),
        },
        {
            "server_id": "srv-002",
            "name": "Beta",
            "registry_source": "source-b",
            "trust_score": 80.5,
            "verdict": "good",
            "risk_tier": "medium",
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
        {
            "server_id": "srv-003",
            "name": "Gamma",
            "registry_source": "source-a",
            "trust_score": 60.2,
            "verdict": "warn",
            "risk_tier": "high",
            "last_seen": datetime(2024, 1, 3, 12, 0, 0),
        },
        {
            "server_id": "srv-004",
            "name": "Delta",
            "registry_source": "source-c",
            "trust_score": 40.0,
            "verdict": "bad",
            "risk_tier": "critical",
            "last_seen": datetime(2024, 1, 4, 12, 0, 0),
        },
        {
            "server_id": "srv-005",
            "name": "Epsilon",
            "registry_source": "source-b",
            "trust_score": 20.1,
            "verdict": "bad",
            "risk_tier": "critical",
            "last_seen": datetime(2024, 1, 5, 12, 0, 0),
        },
    ]

    with TestSessionLocal() as db:
        for rec in seed_data:
            db.add(McpServerRegistry(**rec))
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute the test request
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    response = client.get("/api/directory")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    assert payload["total"] == 5, f"total={payload['total']}"
    assert payload["page"] == 1, f"page={payload['page']}"
    assert payload["per_page"] == 50, f"per_page={payload['per_page']}"
    # First server must have the highest trust_score (95.0)
    first = payload["servers"][0]
    assert first["trust_score"] == 95.0, f"first trust_score={first['trust_score']}"

    print("PASS")