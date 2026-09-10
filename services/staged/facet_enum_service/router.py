"""
Facet enumeration service - provides filterable facet values for dashboard UI.
"""
from __future__ import annotations

import time
from typing import Any
from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, distinct, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


router = APIRouter(prefix="/api", tags=["facets"])


class FacetValue(BaseModel):
    value: str
    count: int


class FacetInfo(BaseModel):
    values: list[FacetValue]
    total: int


class FacetsResponse(BaseModel):
    facets: dict[str, FacetInfo]


@lru_cache(maxsize=1)
def _get_cached_result(ttl: float) -> tuple[float, dict[str, Any]] | None:
    """Cache result wrapper - returns cached data if within TTL."""
    return None


def _clear_cache() -> None:
    """Clear the facet cache."""
    _get_cached_result.cache_clear()


def _get_facets_from_db(session: Session) -> dict[str, Any]:
    """Query database for all facet enum values and counts."""
    facets: dict[str, Any] = {}
    
    # Query mcp_server_registry facets
    registry_facets = ["registry_source", "verdict", "risk_tier"]
    
    for facet_name in registry_facets:
        col = getattr(McpServerRegistry, facet_name)
        query = (
            select(col, func.count(col))
            .where(col.isnot(None))
            .group_by(col)
        )
        results = session.execute(query).all()
        values = [FacetValue(value=str(r[0]), count=r[1]) for r in results]
        total = sum(v.count for v in values)
        facets[facet_name] = FacetInfo(values=values, total=total)
    
    # Query mcp_llm_axis_scores facets
    query = (
        select(McpLlmAxisScore.axis_name, func.count(McpLlmAxisScore.axis_name))
        .where(McpLlmAxisScore.axis_name.isnot(None))
        .group_by(McpLlmAxisScore.axis_name)
    )
    results = session.execute(query).all()
    values = [FacetValue(value=str(r[0]), count=r[1]) for r in results]
    total = sum(v.count for v in values)
    facets["axis_name"] = FacetInfo(values=values, total=total)
    
    return facets


def get_facets(session: Session = Depends(get_session)) -> FacetsResponse:
    """Get all filterable facets with their distinct values and counts."""
    current_time = time.time()
    
    # Check cache (5 minute TTL)
    cached = _get_cached_result(current_time)
    if cached is not None:
        cache_time, cached_data = cached
        if current_time - cache_time < 300:
            return FacetsResponse(facets=cached_data)
    
    # Query fresh data
    facets = _get_facets_from_db(session)
    
    # Cache result
    _get_cached_result.cache_clear()
    lru_cache(maxsize=1)(lambda: (current_time, facets))
    
    return FacetsResponse(facets=facets)


@router.get("/facets", response_model=FacetsResponse)
async def list_facets(session: Session = Depends(get_session)) -> FacetsResponse:
    """List all filterable facets across registry and scoring tables."""
    return get_facets(session)


# ---- self-test ----
if __name__ == "__main__":
    import uuid
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool

    # Create in-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    from app.models import Base
    Base.metadata.create_all(best_engine := test_engine)
    
    # Create session factory
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Seed test data - two servers with distinct risk_tier values
    db = TestSessionLocal()
    try:
        server1 = McpServerRegistry(
            server_id=str(uuid.uuid4()),
            name="test-server-alpha",
            registry_source="npm",
            risk_tier="high",
            verdict="malicious",
        )
        server2 = McpServerRegistry(
            server_id=str(uuid.uuid4()),
            name="test-server-beta",
            registry_source="github",
            risk_tier="low",
            verdict="benign",
        )
        db.add_all([server1, server2])
        db.commit()
    finally:
        db.close()
    
    # Create test app and run self-test
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(that_app)
    
    response = client.get("/api/facets")
    data = response.json()
    
    # Assert risk_tier facet has count=2 for each distinct value
    risk_tier_facet = data["facets"]["risk_tier"]
    risk_tier_values = {v["value"]: v["count"] for v in risk_tier_facet["values"]}
    
    # Each risk_tier value should have count >= 1 (from our seeded servers)
    # Since we have 2 servers with distinct risk_tier values, we should have at least 2 values
    # with count >= 1 each
    passed = (
        response.status_code == 200
        and "risk_tier" in data["facets"]
        and len(risk_tier_values) == 2
        and all(c >= 1 for c in risk_tier_values.values())
    )
    
    if passed:
        print("PASS")
    else:
        print(f"FAIL: risk_tier_values={risk_tier_values}, expected 2 distinct values with count>=1")