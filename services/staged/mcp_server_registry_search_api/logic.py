from typing import List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry


class ServerSearchResult(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str = Field(..., description="Human readable name")
    registry_source: str = Field(..., description="Source of the registry entry")
    url: Optional[str] = Field(None, description="URL of the server")
    risk_tier: Optional[str] = Field(None, description="Risk tier classification")
    last_seen: Optional[str] = Field(
        None, description="ISO formatted timestamp of the last seen event"
    )
    verdict: Optional[str] = Field(None, description="Current verdict")
    confidence: Optional[float] = Field(
        None, description="Confidence score associated with the verdict"
    )


class ServerSearchResponse(BaseModel):
    servers: List[ServerSearchResult] = Field(..., description="List of matching servers")
    total: int = Field(..., description="Total number of matching records")
    limit: int = Field(..., description="Maximum number of records returned")
    offset: int = Field(..., description="Number of records skipped")


def search_servers(
    q: Optional[str] = Query(None, description="Search term for server name"),
    risk_tier: Optional[str] = Query(
        None, description="Filter by risk tier (exact match)"
    ),
    registry_source: Optional[str] = Query(
        None, description="Filter by registry source (exact match)"
    ),
    limit: int = Query(100, ge=1, description="Maximum number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: Session = Depends(get_session),
) -> ServerSearchResponse:
    query = db.query(McpServerRegistry)

    if q:
        query = query.filter(McpServerRegistry.name.ilike(f"%{q}%"))
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)
    if registry_source:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    total = query.count()

    records = (
        query.order_by(desc(McpServerRegistry.last_seen))
        .offset(offset)
        .limit(limit)
        .all()
    )

    servers = [
        ServerSearchResult(
            server_id=rec.server_id,
            name=rec.name,
            registry_source=rec.registry_source,
            url=rec.url,
            risk_tier=rec.risk_tier,
            last_seen=rec.last_seen.isoformat() if rec.last_seen else None,
            verdict=rec.verdict,
            confidence=rec.confidence,
        )
        for rec in records
    ]

    return ServerSearchResponse(
        servers=servers, total=total, limit=limit, offset=offset
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real app DB for testing only)
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    seed_db = TestSessionLocal()
    now = datetime.datetime.utcnow()
    servers = [
        McpServerRegistry(
            server_id="srv-001",
            name="Alpha Server",
            registry_source="source_a",
            url="https://alpha.example.com",
            risk_tier="high",
            last_seen=now - datetime.timedelta(days=1),
            verdict="malicious",
            confidence=0.92,
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="Beta Server",
            registry_source="source_b",
            url="https://beta.example.com",
            risk_tier="medium",
            last_seen=now - datetime.timedelta(days=2),
            verdict="suspicious",
            confidence=0.78,
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="Gamma Server",
            registry_source="source_a",
            url="https://gamma.example.com",
            risk_tier="low",
            last_seen=now - datetime.timedelta(days=3),
            verdict="benign",
            confidence=0.65,
        ),
    ]
    seed_db.add_all(servers)
    seed_db.commit()
    seed_db.close()

    # ------------------------------------------------------------------- #
    # FastAPI app with the search endpoint
    # ------------------------------------------------------------------- #
    app = FastAPI()

    @app.get(
        "/api/servers/search",
        response_model=ServerSearchResponse,
        tags=["mcp_server_registry_search_api"],
    )
    def api_search(
        q: Optional[str] = Query(None),
        risk_tier: Optional[str] = Query(None),
        registry_source: Optional[str] = Query(None),
        limit: int = Query(100, ge=1),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_session),
    ) -> ServerSearchResponse:
        return search_servers(
            q=q,
            risk_tier=risk_tier,
            registry_source=registry_source,
            limit=limit,
            offset=offset,
            db=db,
        )

    # Apply the test DB override
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    resp = client.get("/api/servers/search")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["total"] >= 3, "Expected at least three records"
    assert "server_id" in payload["servers"][0], "Missing server_id in first result"

    print("PASS")