"""freshness_metadata_api.py -- GET /api/servers/{id}/freshness (P1 gate).

THE LINE sequencing (council roadmap 2026-07-02 + docs/DESIGN_NEXT_BUILD_
TARGETS_2026_07.md): freshness surfaces land BEFORE any keyed/agent-facing/
badge surface, and nothing signed/keyed ships against data older than its
declared SLA. This module IS that surface -- the one every keyed/badge feature
(e.g. scorecard_badge_api, merged #1311 but deliberately unmounted) gates on.

Shape: {server_id, last_scored_at, model_version, sla_days, sla_status}
  sla_status: FRESH | STALE | UNKNOWN  (never scored => honest UNKNOWN, no guess)
Computed from mcp_llm_axis_scores.scored_at (the server's newest score row)
vs the declared SLA (env FRESHNESS_SLA_DAYS, default 30).

Exemplar: vuln_exposure_api.py (same per-server GET shape + honest-degrade
semantics). Imports the REAL app data layer -- no stubs.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["freshness"])

from freshness_gate import sla_days as _shared_sla_days  # ONE number (CofC 7/14)

FRESH, STALE, UNKNOWN = "FRESH", "STALE", "UNKNOWN"


def sla_days() -> int:
    """Delegates to freshness_gate: the SLA is declared in exactly one place.

    This module used to default to 30 days while the operational SLA (watch,
    council doctrine) was 7 -- so 11-day-old scores were reported FRESH. That
    divergence WAS the silent-staleness bug. Never re-introduce a second number.
    """
    return _shared_sla_days()


def server_freshness(db: Session, server_id: str,
                     now: Optional[datetime] = None) -> dict:
    """Pure business fn. UNKNOWN when the server has never been scored --
    downstream keyed surfaces MUST treat UNKNOWN as not-fresh (fail closed)."""
    row = db.execute(
        select(McpLlmAxisScore.scored_at, McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id,
               McpLlmAxisScore.scored_at.is_not(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)).first()
    days = sla_days()
    if row is None:
        return {"server_id": server_id, "last_scored_at": None,
                "model_version": None, "sla_days": days, "sla_status": UNKNOWN}
    scored_at, model_version = row
    now = now or datetime.utcnow()
    status = FRESH if now - scored_at <= timedelta(days=days) else STALE
    return {"server_id": server_id, "last_scored_at": scored_at.isoformat(),
            "model_version": model_version, "sla_days": days,
            "sla_status": status}


def is_fresh(db: Session, server_id: str) -> bool:
    """The gate helper for keyed/badge consumers: True ONLY on FRESH.
    STALE and UNKNOWN both fail closed (THE LINE)."""
    return server_freshness(db, server_id)["sla_status"] == FRESH


@router.get("/servers/{server_id}/freshness")
def get_server_freshness(server_id: str, db: Session = Depends(get_session),
                         principal: Principal = Depends(get_principal)) -> dict:
    return server_freshness(db, server_id)


if __name__ == "__main__":
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime.utcnow()
    s.add_all([
        # fresh server: scored yesterday on v3
        McpLlmAxisScore(id=1, server_id="fresh1", axis_name="overall_risk",
                        model_version="v3.0", scored_at=now - timedelta(days=1)),
        # ...with an OLDER row too (newest must win)
        McpLlmAxisScore(id=2, server_id="fresh1", axis_name="auth_strength",
                        model_version="v2.1", scored_at=now - timedelta(days=200)),
        # stale server: newest row far past the SLA
        McpLlmAxisScore(id=3, server_id="stale1", axis_name="overall_risk",
                        model_version="v2.1", scored_at=now - timedelta(days=45)),
    ])
    s.commit()
    f = server_freshness(s, "fresh1")
    assert f["sla_status"] == "FRESH" and f["model_version"] == "v3.0", f
    assert f["sla_days"] == 7 and f["last_scored_at"] is not None
    assert server_freshness(s, "stale1")["sla_status"] == "STALE"
    u = server_freshness(s, "never-scored")
    assert u["sla_status"] == "UNKNOWN" and u["last_scored_at"] is None
    # declared-SLA tunability: at 60d the 45d-old score is FRESH again
    os.environ["FRESHNESS_SLA_DAYS"] = "60"
    assert server_freshness(s, "stale1")["sla_status"] == "FRESH"
    os.environ.pop("FRESHNESS_SLA_DAYS", None)
    # the gate helper fails closed on both STALE and UNKNOWN
    assert is_fresh(s, "fresh1") is True
    assert is_fresh(s, "stale1") is False and is_fresh(s, "nope") is False
    print("PASS")
