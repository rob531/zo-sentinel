"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

from typing import Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def risk_tier_histogram(db: Session) -> Dict[str, int]:
    """Count servers grouped by risk_tier. Real query over the real registry."""
    rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    out: Dict[str, int] = {}
    for tier, count in rows:
        out[tier or "unknown"] = int(count)
    return out
