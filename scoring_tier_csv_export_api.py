"""Scoring tier CSV export API.

Exports all servers with their current risk tier and composite scores as a downloadable CSV file.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/export", tags=["export"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

CSV_COLUMNS = ["server_id", "name", "risk_tier", "overall_score",
               "auth_strength", "capability_breadth", "data_sensitivity",
               "network_egress", "maintainer_trust", "exploit_surface", "scored_at"]


class ExportParams(BaseModel):
    risk_tier: Optional[str] = None
    limit: int = 1000


def _latest_model_version(db: Session) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _iter_rows(db: Session, risk_tier: Optional[str], limit: int):
    """Stream CSV rows by fetching axis scores per server to avoid large JOIN."""
    mv = _latest_model_version(db)
    if not mv:
        return

    # Get scored server IDs, optionally filtered by risk tier from registry
    if risk_tier:
        tier_upper = risk_tier.strip().upper()
        stmt = (
            select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.risk_tier)
            .join(McpLlmAxisScore,
                  McpLlmAxisScore.server_id == McpServerRegistry.server_id)
            .where(
                McpLlmAxisScore.model_version == mv,
                McpLlmAxisScore.axis_name == "overall_risk",
                McpLlmAxisScore.label == tier_upper
            )
            .distinct()
            .limit(limit)
        )
    else:
        stmt = (
            select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.risk_tier)
            .join(McpLlmAxisScore,
                  McpLlmAxisScore.server_id == McpServerRegistry.server_id)
            .where(McpLlmAxisScore.model_version == mv)
            .distinct()
            .limit(limit)
        )

    servers = db.execute(stmt).all()

    for srv in servers:
        sid, name, reg_risk_tier = srv
        # Fetch all 7 axis rows for this server
        axis_rows = db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == sid,
                McpLlmAxisScore.model_version == mv
            )
        ).scalars().all()

        axis_map = {r.axis_name: r for r in axis_rows}

        def _score(ax_name: str) -> Optional[str]:
            r = axis_map.get(ax_name)
            return r.label if r else None

        def _scored_at(ax_name: str) -> Optional[str]:
            r = axis_map.get(ax_name)
            if r and r.scored_at:
                return r.scored_at.isoformat() if hasattr(r.scored_at, 'isoformat') else str(r.scored_at)
            return None

        scored_at = _scored_at("overall_risk") or ""

        yield [
            sid,
            name or "",
            reg_risk_tier or "",
            _score("overall_risk") or "",
            _score("auth_strength") or "",
            _score("capability_breadth") or "",
            _score("data_sensitivity") or "",
            _score("network_egress") or "",
            _score("maintainer_trust") or "",
            _score("exploit_surface") or "",
            scored_at,
        ]


@router.get("/servers/scores")
def export_scores(
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier (e.g., CRITICAL, HIGH, MEDIUM)"),
    limit: int = Query(1000, ge=1, le=10000, description="Maximum rows to export"),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Export all servers with their current risk tier and composite scores as a downloadable CSV file."""
    limit = max(1, min(limit, 10000))

    def _stream():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        yield buf.getvalue()
        buf.truncate(0)
        buf.seek(0)

        for row in _iter_rows(db, risk_tier, limit):
            writer.writerow(row)
            yield buf.getvalue()
            buf.truncate(0)
            buf.seek(0)

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=server_scores.csv",
            "Cache-Control": "no-store",
        },
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="LOW"))
    s.add(McpServerRegistry(server_id="srv2", name="Risky MCP",
                            url="https://example.com/risky",
                            risk_tier="CRITICAL"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "CRITICAL"), ("auth_strength", "WEAK"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "HIGH"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "UNKNOWN"),
                    ("exploit_surface", "HIGH")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "NARROW"), ("data_sensitivity", "LOW"),
                    ("network_egress", "INTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "LOW")), start=101):
        s.add(McpLlmAxisScore(id=_i, server_id="srv2", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
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

    # Test full export
    r = c.get("/export/servers/scores")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert r.headers.get("content-type", "").startswith("text/csv"), \
        f"Expected text/csv, got {r.headers.get('content-type')}"
    body = r.text
    assert "server_id" in body, "Missing header row"
    assert "risk_tier" in body, "Missing risk_tier column"
    assert "overall_score" in body, "Missing overall_score column"
    # Check data rows exist
    lines = body.strip().split("\n")
    assert len(lines) >= 3, f"Expected header + data rows, got {len(lines)} lines"

    # Test risk_tier filter
    r2 = c.get("/export/servers/scores?risk_tier=CRITICAL")
    assert r2.status_code == 200
    body2 = r2.text
    # Only srv1 is CRITICAL on overall_risk
    assert "srv1" in body2 or "Stripe" in body2, f"Expected srv1 (CRITICAL), got: {body2[:200]}"

    # Test limit
    r3 = c.get("/export/servers/scores?limit=1")
    assert r3.status_code == 200

    print("PASS")
