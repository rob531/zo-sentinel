"""
services/staged/threat_intel_indicator_timeline_api/contract.py

FastAPI contract for the Threat Intel Indicator Timeline API.
Provides a self‑test that runs against an in‑memory SQLite database.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, outerjoin, select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real data layer imports (must not be re‑implemented)
# ----------------------------------------------------------------------
from app.db import Base, get_session  # noqa: F401  (Base needed for test DB creation)
from app.models import (
    McpServerRegistry,
    ThreatIntelRef,
    VulnAdvisory,
    VulnLink,
)

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class LinkModel(BaseModel):
    server_id: Optional[int] = None
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None
    match_basis: Optional[str] = None
    match_confidence: Optional[float] = None
    advisory_id: Optional[int] = None
    advisory_severity: Optional[str] = None
    advisory_summary: Optional[str] = None


class IndicatorModel(BaseModel):
    indicator_id: int = Field(..., alias="id")
    indicator_type: Optional[str] = None
    indicator_value: Optional[str] = None
    pulse_id: Optional[int] = None
    pulse_name: Optional[str] = None
    pulse_created: Optional[datetime.datetime] = None
    is_aggregator: Optional[bool] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    fetched_at: Optional[datetime.datetime] = None
    links: List[LinkModel] = []


class TimelineResponse(BaseModel):
    days: int
    count: int
    indicators: List[IndicatorModel]


# ----------------------------------------------------------------------
# Router / endpoint implementation
# ----------------------------------------------------------------------
router = APIRouter()


@router.get(
    "/threat/indicators",
    response_model=TimelineResponse,
    summary="List threat‑intel indicators with linked vulnerability data",
)
def list_indicators(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    indicator_type: Optional[str] = Query(
        None, description="Filter by indicator type"
    ),
    server_id: Optional[int] = Query(
        None, description="Filter by server identifier"
    ),
    session: Session = Depends(get_session),
) -> TimelineResponse:
    """
    Return indicators fetched in the last *days* days, optionally filtered
    by *indicator_type* and/or *server_id*.
    """
    # ------------------------------------------------------------------
    # Build the query with LEFT OUTER joins
    # ------------------------------------------------------------------
    tir = ThreatIntelRef.__table__
    vl = VulnLink.__table__
    va = VulnAdvisory.__table__
    sr = McpServerRegistry.__table__

    # SQLite uses `datetime('now', '-N days')`; for Postgres `now() - interval`
    cutoff_expr = func.datetime("now", f"-{days} days")

    stmt = (
        select(
            tir.c.id.label("indicator_id"),
            tir.c.indicator_type,
            tir.c.indicator_value,
            tir.c.pulse_id,
            tir.c.pulse_name,
            tir.c.pulse_created,
            tir.c.is_aggregator,
            tir.c.source,
            tir.c.source_url,
            tir.c.fetched_at,
            vl.c.server_id,
            sr.c.name.label("server_name"),
            sr.c.risk_tier,
            vl.c.match_basis,
            vl.c.match_confidence,
            va.c.id.label("advisory_id"),
            va.c.severity.label("advisory_severity"),
            va.c.summary.label("advisory_summary"),
        )
        .select_from(
            tir.outerjoin(vl, tir.c.indicator_value == vl.c.match_value)
            .outerjoin(va, vl.c.advisory_id == va.c.id)
            .outerjoin(sr, vl.c.server_id == sr.c.server_id)
        )
        .where(tir.c.fetched_at >= cutoff_expr)
    )

    if indicator_type:
        stmt = stmt.where(tir.c.indicator_type == indicator_type)
    if server_id:
        stmt = stmt.where(sr.c.server_id == server_id)

    rows = session.execute(stmt).fetchall()

    # ------------------------------------------------------------------
    # Assemble the hierarchical response
    # ------------------------------------------------------------------
    indicator_map: dict[int, IndicatorModel] = {}

    for row in rows:
        ind_id = row.indicator_id
        if ind_id not in indicator_map:
            indicator_map[ind_id] = IndicatorModel(
                id=ind_id,
                indicator_type=row.indicator_type,
                indicator_value=row.indicator_value,
                pulse_id=row.pulse_id,
                pulse_name=row.pulse_name,
                pulse_created=row.pulse_created,
                is_aggregator=row.is_aggregator,
                source=row.source,
                source_url=row.source_url,
                fetched_at=row.fetched_at,
                links=[],
            )
        # Build link if any of the link‑related columns are present
        if row.server_id is not None or row.advisory_id is not None:
            link = LinkModel(
                server_id=row.server_id,
                server_name=row.server_name,
                risk_tier=row.risk_tier,
                match_basis=row.match_basis,
                match_confidence=row.match_confidence,
                advisory_id=row.advisory_id,
                advisory_severity=row.advisory_severity,
                advisory_summary=row.advisory_summary,
            )
            indicator_map[ind_id].links.append(link)

    indicators = list(indicator_map.values())
    response = TimelineResponse(
        days=days,
        count=len(indicators),
        indicators=indicators,
    )
    return response


# ----------------------------------------------------------------------
# FastAPI application (used by the self‑test)
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router, prefix="/api")


# ----------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine with a StaticPool (required for
    # FastAPI's TestClient to reuse the same connection across threads)
    # ------------------------------------------------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    TEST_DB_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------
    # Helper to provide a session for the FastAPI dependency
    # ------------------------------------------------------------------
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the real get_session dependency with the test version
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    now = datetime.datetime.utcnow()
    with TestSessionLocal() as db:
        # ThreatIntelRef entries
        tir1 = ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.2.3.4",
            pulse_id=100,
            pulse_name="Sample Pulse",
            pulse_created=now - datetime.timedelta(days=1),
            is_aggregator=True,
            source="source-a",
            source_url="http://example.com/a",
            fetched_at=now - datetime.timedelta(hours=2),
        )
        tir2 = ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id=101,
            pulse_name="Another Pulse",
            pulse_created=now - datetime.timedelta(days=2),
            is_aggregator=False,
            source="source-b",
            source_url="http://example.com/b",
            fetched_at=now - datetime.timedelta(hours=3),
        )
        db.add_all([tir1, tir2])

        # Server registry entry
        server = McpServerRegistry(
            server_id=10,
            name="test-server",
            risk_tier="high",
        )
        db.add(server)

        # Vulnerability advisory
        advisory = VulnAdvisory(
            id=200,
            severity="critical",
            summary="Test advisory summary",
            feed="test-feed",
            ecosystem="linux",
            package="testpkg",
            source_url="http://advisory.example.com/200",
            published_at=now - datetime.timedelta(days=5),
        )
        db.add(advisory)

        # Vulnerability link (links tir1 to advisory & server)
        link = VulnLink(
            id=300,
            advisory_id=advisory.id,
            server_id=server.server_id,
            match_basis="exact",
            match_confidence=0.99,
            match_value=tir1.indicator_value,
        )
        db.add(link)

        db.commit()

    # ------------------------------------------------------------------
    # Run acceptance tests using TestClient
    # ------------------------------------------------------------------
    client = TestClient(app)

    # 1. Basic request – no filters
    resp = client.get("/api/threat/indicators", params={"days": 7})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["count"] == 2, f"Expected 2 indicators, got {data['count']}"
    # Find the aggregator indicator (is_aggregator true)
    agg = next(i for i in data["indicators"] if i["is_aggregator"])
    assert len(agg["links"]) == 1, "Aggregator indicator should have exactly one link"
    link = agg["links"][0]
    assert link["server_id"] is not None, "Link must contain a server_id"
    assert link["advisory_severity"] is not None, "Link must contain advisory_severity"

    # 2. Type filter – should narrow to the non‑aggregator indicator
    resp2 = client.get(
        "/api/threat/indicators",
        params={"days": 7, "indicator_type": "domain"},
    )
    assert resp2.status_code == 200, f"Unexpected status {resp2.status_code}"
    data2 = resp2.json()
    assert data2["count"] == 1, f"Expected 1 indicator after type filter, got {data2['count']}"

    print("PASS")