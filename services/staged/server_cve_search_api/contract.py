"""
services.staged.server_cve_search_api.contract
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table, MetaData, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# --------------------------------------------------------------------------- #
# Real application dependencies
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for FastAPI dependency)

# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/cves",
    response_model=dict,
    summary="Retrieve CVEs for a given server",
)
async def get_server_cves(
    server_id: int,
    severity: Optional[str] = Query(
        None,
        description="Filter by severity (low, medium, high, critical)",
        regex="^(low|medium|high|critical)$",
    ),
    days_since_published: Optional[int] = Query(
        None,
        ge=0,
        description="Only include CVEs published within the last N days",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """
    Return a payload containing server information and a list of CVEs
    associated with the server.  The data is gathered by joining
    `vuln_links` → `vuln_advisories` → `McpServerRegistry`.
    """
    # ------------------------------------------------------------------- #
    # Reflect tables (works with both production Postgres and the test DB)
    # ------------------------------------------------------------------- #
    bind = session.get_bind()
    metadata = MetaData()
    server_tbl = Table("McpServerRegistry", metadata, autoload_with=bind)
    advisory_tbl = Table("vuln_advisories", metadata, autoload_with=bind)
    link_tbl = Table("vuln_links", metadata, autoload_with=bind)

    # ------------------------------------------------------------------- #
    # Validate server existence
    # ------------------------------------------------------------------- #
    stmt_server = (
        select(server_tbl.c.server_id, server_tbl.c.server_name)
        .where(server_tbl.c.server_id == server_id)
        .limit(1)
    )
    server_row = session.execute(stmt_server).first()
    if not server_row:
        raise HTTPException(status_code=404, detail="Server not found")

    server_name = server_row.server_name

    # ------------------------------------------------------------------- #
    # Build CVE query
    # ------------------------------------------------------------------- #
    stmt = (
        select(
            advisory_tbl.c.advisory_id.label("id"),
            advisory_tbl.c.summary,
            advisory_tbl.c.severity,
            advisory_tbl.c.severity_score,
            advisory_tbl.c.published_at,
            advisory_tbl.c.source_url,
            advisory_tbl.c.match_basis,
            advisory_tbl.c.match_confidence,
            advisory_tbl.c.affected_ranges,
        )
        .select_from(
            link_tbl.join(
                advisory_tbl,
                link_tbl.c.advisory_id == advisory_tbl.c.advisory_id,
            )
        )
        .where(link_tbl.c.server_id == server_id)
    )

    if severity:
        stmt = stmt.where(advisory_tbl.c.severity.ilike(severity))

    if days_since_published is not None:
        cutoff = datetime.utcnow() - timedelta(days=days_since_published)
        stmt = stmt.where(advisory_tbl.c.published_at >= cutoff)

    stmt = stmt.order_by(advisory_tbl.c.published_at.desc())

    rows = session.execute(stmt).fetchall()

    cves: List[dict] = [
        {
            "id": r.id,
            "summary": r.summary,
            "severity": r.severity,
            "severity_score": r.severity_score,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "source_url": r.source_url,
            "match_basis": r.match_basis,
            "match_confidence": r.match_confidence,
            "affected_ranges": r.affected_ranges,
        }
        for r in rows
    ]

    return {"server_id": server_id, "server_name": server_name, "cves": cves}


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.server_cve_search_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app and override the DB dependency with an
    # in‑memory SQLite database (StaticPool) that mimics the real schema.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite engine and reflect the same tables we use
    # in the endpoint.  The tables are defined explicitly here – this is
    # only for the self‑test; production uses the real Postgres tables.
    # ------------------------------------------------------------------- #
    test_engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()

    server_tbl = Table(
        "McpServerRegistry",
        test_metadata,
        Column("server_id", Integer, primary_key=True),
        Column("server_name", String, nullable=False),
    )
    advisory_tbl = Table(
        "vuln_advisories",
        test_metadata,
        Column("advisory_id", Integer, primary_key=True),
        Column("summary", String),
        Column("severity", String),
        Column("severity_score", Float),
        Column("published_at", DateTime),
        Column("source_url", String),
        Column("match_basis", String),
        Column("match_confidence", Float),
        Column("affected_ranges", String),
    )
    link_tbl = Table(
        "vuln_links",
        test_metadata,
        Column("link_id", Integer, primary_key=True),
        Column("server_id", Integer, ForeignKey("McpServerRegistry.server_id")),
        Column("advisory_id", Integer, ForeignKey("vuln_advisories.advisory_id")),
    )
    test_metadata.create_all(bind=test_engine)

    # ------------------------------------------------------------------- #
    # Populate the test DB with deterministic data.
    # ------------------------------------------------------------------- #
    with test_engine.begin() as conn:
        # Two servers
        conn.execute(server_tbl.insert(), [
            {"server_id": 1, "server_name": "alpha"},
            {"server_id": 2, "server_name": "beta"},
        ])

        # Three advisories (four links total)
        now = datetime.utcnow()
        adv_data = [
            {
                "advisory_id": 10,
                "summary": "CVE-2023-0001",
                "severity": "high",
                "severity_score": 7.5,
                "published_at": now - timedelta(days=1),
                "source_url": "http://example.com/10",
                "match_basis": "pkg",
                "match_confidence": 0.9,
                "affected_ranges": ">=1.0,<2.0",
            },
            {
                "advisory_id": 11,
                "summary": "CVE-2023-0002",
                "severity": "medium",
                "severity_score": 5.0,
                "published_at": now - timedelta(days=10),
                "source_url": "http://example.com/11",
                "match_basis": "pkg",
                "match_confidence": 0.8,
                "affected_ranges": ">=2.0,<3.0",
            },
            {
                "advisory_id": 12,
                "summary": "CVE-2023-0003",
                "severity": "critical",
                "severity_score": 9.8,
                "published_at": now - timedelta(days=3),
                "source_url": "http://example.com/12",
                "match_basis": "pkg",
                "match_confidence": 0.95,
                "affected_ranges": ">=3.0,<4.0",
            },
        ]
        conn.execute(advisory_tbl.insert(), adv_data)

        # Links
        conn.execute(link_tbl.insert(), [
            {"link_id": 100, "server_id": 1, "advisory_id": 10},
            {"link_id": 101, "server_id": 1, "advisory_id": 11},
            {"link_id": 102, "server_id": 1, "advisory_id": 12},
            {"link_id": 103, "server_id": 2, "advisory_id": 10},
        ])

    # ------------------------------------------------------------------- #
    # Dependency override: provide a Session that uses the test engine.
    # ------------------------------------------------------------------- #
    from sqlalchemy.orm import sessionmaker

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    async def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Run acceptance checks.
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    # Basic request – expect at least one CVE
    resp = client.get("/api/servers/1/cves")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["server_id"] == 1
    assert len(payload["cves"]) >= 1

    # Severity filter – should return only the high severity advisory
    resp = client.get("/api/servers/1/cves?severity=high")
    assert resp.status_code == 200, f"Severity filter status {resp.status_code}"
    payload = resp.json()
    assert len(payload["cves"]) == 1
    assert payload["cves"][0]["severity"] == "high"

    print("PASS")
    sys.exit(0)