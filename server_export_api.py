# deps: fastapi
"""Bulk export endpoint for MCP server registry.

GET /servers/export returns mcp_server_registry rows as CSV or JSON.
Supports format, risk_tier, source, limit, offset query params.
Reads from app.db / app.models -- no inline stubs.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["servers"])

COLUMNS = ("server_id", "name", "registry_source", "url", "verdict",
           "risk_tier", "trust_score", "confidence", "last_assessed", "last_seen")


@router.get("/servers/export")
def export_servers(
    format: str = Query("json", pattern="^(csv|json)$"),
    risk_tier: str = Query("", description="Filter by risk_tier (e.g. HIGH, MEDIUM, LOW)"),
    source: str = Query("", description="Filter by registry_source"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> Response:
    """Export all (or filtered) mcp_server_registry rows as CSV or JSON."""
    conds = []
    if risk_tier.strip():
        conds.append(McpServerRegistry.risk_tier == risk_tier.strip())
    if source.strip():
        conds.append(McpServerRegistry.registry_source == source.strip())

    stmt = select(McpServerRegistry)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(func.lower(McpServerRegistry.name)).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()

    records = []
    for r in rows:
        records.append({
            "server_id": r.server_id,
            "name": r.name,
            "registry_source": r.registry_source,
            "url": r.url,
            "verdict": r.verdict,
            "risk_tier": r.risk_tier,
            "trust_score": r.trust_score,
            "confidence": r.confidence,
            "last_assessed": r.last_assessed.isoformat() if r.last_assessed else None,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        })

    if format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=servers.csv"})
    return {"servers": records, "count": len(records), "offset": offset, "limit": limit}


if __name__ == "__main__":
    import tempfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base, McpServerRegistry as _M

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = db_file.name
    db_file.close()
    eng = create_engine(f"sqlite:///{db_path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(_M(server_id="srv1", name="Stripe MCP",
              url="https://github.com/stripe/agent-toolkit",
              registry_source="github", risk_tier="MEDIUM",
              trust_score=0.9, confidence=0.95))
    s.add(_M(server_id="srv2", name="Evil MCP",
              url="https://evil.example.com",
              registry_source="user_submission", risk_tier="CRITICAL",
              trust_score=0.1, confidence=0.8))
    s.add(_M(server_id="srv3", name="Test MCP",
              url="https://test.example.com",
              registry_source="npm", risk_tier="HIGH",
              trust_score=0.5, confidence=0.7))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: JSON default
    r = c.get("/api/servers/export"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 3, j; assert len(j["servers"]) == 3

    # Format param: CSV
    r = c.get("/api/servers/export?format=csv"); assert r.status_code == 200, r.text
    assert "server_id" in r.text; assert "Stripe MCP" in r.text
    assert "text/csv" in r.headers["content-type"]

    # Format param: JSON explicit
    r = c.get("/api/servers/export?format=json"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 3

    # Risk tier filter reduces results
    r = c.get("/api/servers/export?risk_tier=CRITICAL"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 1; assert j["servers"][0]["name"] == "Evil MCP"

    # Source filter
    r = c.get("/api/servers/export?source=github"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 1; assert j["servers"][0]["registry_source"] == "github"

    # Pagination
    r = c.get("/api/servers/export?limit=2&offset=0"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 2; assert j["limit"] == 2

    # Edge case: empty result
    r = c.get("/api/servers/export?risk_tier=NONE"); assert r.status_code == 200, r.text
    j = r.json(); assert j["count"] == 0; assert j["servers"] == []

    import os
    os.unlink(db_path)
    print("PASS")
