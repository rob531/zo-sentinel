# deps: fastapi, pydantic, sqlalchemy
"""router.py -- registry_search_api.

Search and browse MCP servers from the registry.
Public endpoint (auth=public per directive).
Reads from mcp_server_registry via app/db get_session.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_search_api"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SearchResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_assessed: Optional[datetime] = None
    trust_score: Optional[float] = None


class SearchResponse(BaseModel):
    items: List[SearchResultItem]
    total: int
    page: int
    limit: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/registry/search", response_model=SearchResponse)
def registry_search(
    q: Optional[str] = Query(None, description="Search term matching name, description, or URL"),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search the MCP server registry with optional filters and pagination."""
    query = db.query(McpServerRegistry)

    if q:
        term = f"%{q}%"
        query = query.filter(
            McpServerRegistry.name.ilike(term)
            | McpServerRegistry.description.ilike(term)
            | McpServerRegistry.url.ilike(term)
        )

    if source:
        query = query.filter(McpServerRegistry.registry_source == source)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    total = query.count()

    offset = (page - 1) * limit
    results = query.offset(offset).limit(limit).all()

    items = [
        SearchResultItem(
            server_id=r.server_id,
            name=r.name,
            registry_source=r.registry_source,
            risk_tier=r.risk_tier,
            verdict=r.verdict,
            last_assessed=r.last_assessed,
            trust_score=r.trust_score,
        )
        for r in results
    ]

    return SearchResponse(items=items, total=total, page=page, limit=limit)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
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
    TestSessionLocal = sessionmaker(bind=engine)

    # Seed data
    session = TestSessionLocal()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-001",
                name="Arctic Hub",
                description="Northern data processing",
                url="https://arctic.example.com",
                registry_source="public_registry",
                risk_tier="low",
                verdict="trusted",
                last_assessed=datetime.now(),
                trust_score=0.95,
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Storm Core",
                description="Weather analysis service",
                url="https://storm.example.com",
                registry_source="cloud_index",
                risk_tier="medium",
                verdict="unknown",
                last_assessed=datetime.now(),
                trust_score=0.60,
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Echo Server",
                description="Audio processing and analysis",
                url="https://echo.example.com",
                registry_source="public_registry",
                risk_tier="high",
                verdict="untrusted",
                last_assessed=datetime.now(),
                trust_score=0.25,
            ),
            McpServerRegistry(
                server_id="srv-004",
                name="Vault Seal",
                description="Secure storage service",
                url="https://vault.example.com",
                registry_source="cloud_index",
                risk_tier="critical",
                verdict="unknown",
                last_assessed=datetime.now(),
                trust_score=0.50,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test 1: unfiltered search
    resp = client.get("/api/registry/search")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["total"] >= 3, f"Expected total >= 3, got {data['total']}"
    names = [item["name"] for item in data["items"]]
    assert "Storm Core" in names, f"Expected 'Storm Core' in results, got {names}"

    # Test 2: text search
    resp2 = client.get("/api/registry/search", params={"q": "Weather"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] >= 1
    assert data2["items"][0]["name"] == "Storm Core"

    # Test 3: source filter
    resp3 = client.get("/api/registry/search", params={"source": "public_registry"})
    assert resp3.status_code == 200
    data3 = resp3.json()
    for item in data3["items"]:
        assert item["registry_source"] == "public_registry"

    # Test 4: risk_tier filter
    resp4 = client.get("/api/registry/search", params={"risk_tier": "high"})
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["total"] >= 1
    assert data4["items"][0]["risk_tier"] == "high"

    # Test 5: pagination
    resp5 = client.get("/api/registry/search", params={"page": 1, "limit": 2})
    assert resp5.status_code == 200
    data5 = resp5.json()
    assert len(data5["items"]) <= 2
    assert data5["page"] == 1
    assert data5["limit"] == 2

    print("PASS")
    sys.exit(0)
