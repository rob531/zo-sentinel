# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Export Service.

Provides CSV export endpoints for risk tier data:
  - GET /api/risk_tier_export/full        -- all servers with risk tier + axis scores
  - GET /api/risk_tier_export/summary      -- tier distribution counts
  - GET /api/risk_tier_export/by_tier      -- servers grouped by tier

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry /
  mcp_llm_axis_scores / orgs.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Ensure repo root on path for `app` imports when run as script
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_tier_export_service"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _csv_response(rows: List, headers: List[str]) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    output.seek(0)
    today = datetime.utcnow().strftime("%Y%m%d")
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=risk_tier_export_{today}.csv"
            )
        },
    )


# --------------------------------------------------------------------------- #
# Full export
# --------------------------------------------------------------------------- #

FULL_HEADERS = [
    "server_id", "name", "registry_source", "url", "description",
    "verdict", "risk_tier", "trust_score", "confidence",
    "p_top", "p_critical", "p_danger",
    "scan_count", "last_scanned", "last_seen", "first_seen",
]


@router.get("/risk_tier_export/full")
def export_full(
    risk_tier: str | None = Query(default=None, description="Filter by risk tier"),
    verdict: str | None = Query(default=None, description="Filter by verdict"),
    registry_source: str | None = Query(default=None, description="Filter by registry source"),
    limit: int = Query(default=10000, le=50000),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Export all servers with risk tier and latest overall_risk axis scores."""
    rows_q = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.description,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.confidence,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_seen,
            McpServerRegistry.first_seen,
        )
        .outerjoin(
            McpLlmAxisScore,
            (McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            & (McpLlmAxisScore.axis_name == "overall_risk"),
        )
        .order_by(McpServerRegistry.last_seen.desc())
    )

    if risk_tier:
        rows_q = rows_q.filter(McpServerRegistry.risk_tier == risk_tier)
    if verdict:
        rows_q = rows_q.filter(McpServerRegistry.verdict == verdict)
    if registry_source:
        rows_q = rows_q.filter(McpServerRegistry.registry_source == registry_source)

    raw = rows_q.limit(limit).all()

    def _fmt(val):
        return val.isoformat() if hasattr(val, "isoformat") else (val or "")

    csv_rows = [
        [
            r.server_id or "",
            r.name or "",
            r.registry_source or "",
            r.url or "",
            r.description or "",
            r.verdict or "",
            r.risk_tier or "",
            r.trust_score or "",
            r.confidence or "",
            r.p_top or "",
            r.p_critical or "",
            r.p_danger or "",
            r.scan_count or 0,
            _fmt(r.last_scanned),
            _fmt(r.last_seen),
            _fmt(r.first_seen),
        ]
        for r in raw
    ]

    return _csv_response(csv_rows, FULL_HEADERS)


# --------------------------------------------------------------------------- #
# Summary: tier distribution
# --------------------------------------------------------------------------- #

@router.get("/risk_tier_export/summary")
def export_summary(
    registry_source: str | None = Query(default=None, description="Filter by registry source"),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Export tier distribution counts, optionally broken down by source."""
    rows_q = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
            func.avg(McpServerRegistry.confidence).label("avg_confidence"),
            McpServerRegistry.registry_source,
        )
        .group_by(
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
        .order_by(McpServerRegistry.risk_tier)
    )

    if registry_source:
        rows_q = rows_q.filter(McpServerRegistry.registry_source == registry_source)

    headers = ["risk_tier", "server_count", "avg_trust_score", "avg_confidence", "registry_source"]
    csv_rows = [
        [
            r.risk_tier or "UNKNOWN",
            r.count,
            f"{r.avg_trust_score:.4f}" if r.avg_trust_score is not None else "",
            f"{r.avg_confidence:.4f}" if r.avg_confidence is not None else "",
            r.registry_source or "",
        ]
        for r in rows_q.all()
    ]
    return _csv_response(csv_rows, headers)


# --------------------------------------------------------------------------- #
# By-tier breakdown
# --------------------------------------------------------------------------- #

@router.get("/risk_tier_export/by_tier")
def export_by_tier(
    risk_tier: str = Query(..., description="Risk tier to export"),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Export all servers for a specific risk tier with latest axis scores."""
    rows_q = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
            McpServerRegistry.confidence,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_seen,
        )
        .outerjoin(
            McpLlmAxisScore,
            (McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            & (McpLlmAxisScore.axis_name == "overall_risk"),
        )
        .filter(McpServerRegistry.risk_tier == risk_tier)
        .order_by(McpServerRegistry.last_seen.desc())
    )

    headers = [
        "server_id", "name", "registry_source", "url", "verdict",
        "trust_score", "confidence", "p_top", "p_critical", "p_danger",
        "last_scanned", "last_seen",
    ]

    def _fmt(val):
        return val.isoformat() if hasattr(val, "isoformat") else (val or "")

    csv_rows = [
        [
            r.server_id or "",
            r.name or "",
            r.registry_source or "",
            r.url or "",
            r.verdict or "",
            r.trust_score or "",
            r.confidence or "",
            r.p_top or "",
            r.p_critical or "",
            r.p_danger or "",
            _fmt(r.last_scanned),
            _fmt(r.last_seen),
        ]
        for r in rows_q.all()
    ]
    return _csv_response(csv_rows, headers)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        db.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, registry_source, url, verdict, trust_score, "
                "confidence, risk_tier, scan_count, first_seen, last_seen, last_scanned) VALUES "
                "('srv1','Alpha','github','https://github.com/alpha','safe',0.9,0.95,'LOW',1,'2024-01-01','2024-06-15','2024-06-14'),"
                "('srv2','Beta','npm','https://npmjs.com/beta','suspicious',0.7,0.85,'MEDIUM',2,'2024-02-01','2024-06-20','2024-06-19'),"
                "('srv3','Gamma','github','https://github.com/gamma','malicious',0.4,0.75,'HIGH',3,'2024-03-01','2024-06-25','2024-06-24'),"
                "('srv4','Delta','npm','https://npmjs.com/delta','safe',0.85,0.92,'LOW',1,'2024-01-15','2024-06-26','2024-06-25')"
            )
        )
        db.execute(
            text(
                "INSERT INTO mcp_llm_axis_scores "
                "(id, server_id, axis_name, model_version, p_top, p_critical, p_danger) VALUES "
                "(1,'srv1','overall_risk','v1.0',0.8,0.1,0.05),"
                "(2,'srv2','overall_risk','v1.0',0.6,0.3,0.1),"
                "(3,'srv3','overall_risk','v1.0',0.4,0.5,0.3),"
                "(4,'srv4','overall_risk','v1.0',0.75,0.15,0.05)"
            )
        )
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    _that_app.dependency_overrides[get_session] = _override_session
    _c = TestClient(_that_app)

    # full export
    resp = _c.get("/api/risk_tier_export/full")
    assert resp.status_code == 200, f"full: {resp.status_code}"
    lines = resp.text.splitlines()
    assert len(lines) >= 5, f"full: expected >=5 lines, got {len(lines)}"
    assert "attachment; filename=risk_tier_export_" in resp.headers["content-disposition"]

    # full export with filter
    resp2 = _c.get("/api/risk_tier_export/full?risk_tier=HIGH")
    assert resp2.status_code == 200
    assert len(resp2.text.splitlines()) == 2, f"HIGH filter: {resp2.text}"

    # full export with source filter
    resp3 = _c.get("/api/risk_tier_export/full?registry_source=npm")
    assert resp3.status_code == 200
    assert len(resp3.text.splitlines()) == 3, f"npm filter: {resp3.text}"

    # summary
    resp4 = _c.get("/api/risk_tier_export/summary")
    assert resp4.status_code == 200
    lines4 = resp4.text.splitlines()
    assert len(lines4) >= 2, f"summary: {lines4}"

    # summary with source filter
    resp5 = _c.get("/api/risk_tier_export/summary?registry_source=github")
    assert resp5.status_code == 200

    # by_tier
    resp6 = _c.get("/api/risk_tier_export/by_tier?risk_tier=LOW")
    assert resp6.status_code == 200
    assert len(resp6.text.splitlines()) == 3, f"LOW tier: {resp6.text}"

    resp7 = _c.get("/api/risk_tier_export/by_tier?risk_tier=HIGH")
    assert resp7.status_code == 200
    assert len(resp7.text.splitlines()) == 2, f"HIGH tier: {resp7.text}"

    print("PASS")
