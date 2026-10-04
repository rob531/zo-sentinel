# deps: fastapi, pydantic, sqlalchemy
"""Vuln Linkage Coverage API.

GET /api/vuln/linkage-coverage
  Returns per-server vulnerability linkage coverage: for every server that has
  at least one vuln-link row, the count of distinct advisories linked, total
  advisories in the system, coverage %, and the most-recent link timestamp.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on VulnLink / VulnAdvisory.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api/vuln", tags=["vuln_linkage_coverage_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class ServerLinkageStats(BaseModel):
    server_id: str = Field(..., description="Server identifier (from mcp_server_registry)")
    linked_count: int = Field(..., description="Number of distinct advisories linked to this server")
    total_advisories: int = Field(..., description="Total advisories in the system")
    coverage_pct: float = Field(..., ge=0.0, le=100.0, description="Coverage percentage")
    last_linked_at: Optional[datetime] = Field(
        None, description="ISO 8601 timestamp of the most recent link"
    )


class LinkageCoverageResponse(BaseModel):
    servers: List[ServerLinkageStats] = Field(
        ..., description="Per-server linkage coverage rows"
    )


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #


def get_linkage_coverage(db: Session) -> LinkageCoverageResponse:
    """
    Aggregate vuln linkage coverage per server.

    coverage_pct = (distinct advisories linked to server) / (total advisories) * 100
    """
    # Total advisories in the system
    total_advisories: int = db.execute(
        select(func.count(VulnAdvisory.id))
    ).scalar() or 0

    # Per-server linkage stats via a plain SQL query (most portable)
    rows = db.execute(
        text("""
            SELECT
                vl.server_id,
                COUNT(DISTINCT vl.advisory_id) AS linked_count,
                MAX(vl.linked_at)               AS last_linked_at
            FROM vuln_links vl
            GROUP BY vl.server_id
            ORDER BY vl.server_id
        """)
    ).fetchall()

    servers = []
    for row in rows:
        linked_count = row.linked_count or 0
        if total_advisories > 0:
            coverage_pct = round((linked_count / total_advisories) * 100, 2)
        else:
            coverage_pct = 0.0
        servers.append(
            ServerLinkageStats(
                server_id=row.server_id,
                linked_count=linked_count,
                total_advisories=total_advisories,
                coverage_pct=coverage_pct,
                last_linked_at=row.last_linked_at,
            )
        )

    return LinkageCoverageResponse(servers=servers)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/linkage-coverage", response_model=LinkageCoverageResponse)
def linkage_coverage_endpoint(
    session: Session = Depends(get_session),
) -> LinkageCoverageResponse:
    return get_linkage_coverage(session)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    db = TestingSessionLocal()

    # 5 advisories
    db.execute(
        text("""
            INSERT INTO vuln_advisories
                (id, feed, summary, severity, ecosystem, package,
                 affected_ranges, aliases, source_url, published_at,
                 fetched_at, identities, content_hash)
            VALUES
                ('ADV-001', 'nvd', 'Adv A', 'HIGH',   'npm',  'pkg-a',
                 '[]', '[]', 'http://a.com', '2024-01-01', '2024-01-02', '[]', 'hash1'),
                ('ADV-002', 'nvd', 'Adv B', 'MEDIUM', 'npm',  'pkg-b',
                 '[]', '[]', 'http://b.com', '2024-01-01', '2024-01-02', '[]', 'hash2'),
                ('ADV-003', 'ghsa','Adv C', 'LOW',    'pip',  'pkg-c',
                 '[]', '[]', 'http://c.com', '2024-01-01', '2024-01-02', '[]', 'hash3'),
                ('ADV-004', 'nvd', 'Adv D', 'CRITICAL','npm', 'pkg-d',
                 '[]', '[]', 'http://d.com', '2024-01-01', '2024-01-02', '[]', 'hash4'),
                ('ADV-005', 'ghsa','Adv E', 'HIGH',   'npm',  'pkg-e',
                 '[]', '[]', 'http://e.com', '2024-01-01', '2024-01-02', '[]', 'hash5')
        """)
    )
    db.commit()

    # 3 links: srv-1 → ADV-001 + ADV-002,  srv-2 → ADV-003
    db.execute(
        text("""
            INSERT INTO vuln_links
                (id, advisory_id, server_id, match_basis, match_value,
                 match_confidence, linked_at)
            VALUES
                (1, 'ADV-001', 'srv-1', 'exact', 'pkg-a', 0.95, '2024-06-01 10:00:00'),
                (2, 'ADV-002', 'srv-1', 'fuzzy', 'pkg-b', 0.80, '2024-06-02 10:00:00'),
                (3, 'ADV-003', 'srv-2', 'exact', 'pkg-c', 0.90, '2024-06-03 10:00:00')
        """)
    )
    db.commit()
    db.close()

    # Override session and run tests
    from app.main import app

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/vuln/linkage-coverage")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    data = response.json()
    servers = data.get("servers", [])
    assert len(servers) == 2, f"Expected 2 servers, got {len(servers)}"

    by_id = {s["server_id"]: s for s in servers}

    s1 = by_id.get("srv-1")
    assert s1 is not None, "srv-1 not in response"
    assert s1["linked_count"] == 2, f"srv-1 linked_count: expected 2, got {s1['linked_count']}"
    assert s1["total_advisories"] == 5, f"srv-1 total_advisories: expected 5, got {s1['total_advisories']}"
    assert s1["coverage_pct"] == 40.0, f"srv-1 coverage_pct: expected 40.0, got {s1['coverage_pct']}"
    assert s1["last_linked_at"] is not None

    s2 = by_id.get("srv-2")
    assert s2 is not None, "srv-2 not in response"
    assert s2["linked_count"] == 1, f"srv-2 linked_count: expected 1, got {s2['linked_count']}"
    assert s2["total_advisories"] == 5
    assert s2["coverage_pct"] == 20.0, f"srv-2 coverage_pct: expected 20.0, got {s2['coverage_pct']}"

    # srv-3 has no links → should NOT appear (only servers with links are returned)
    assert "srv-3" not in by_id

    app.dependency_overrides.clear()
    print("PASS")
