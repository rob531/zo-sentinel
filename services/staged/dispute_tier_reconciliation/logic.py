"""logic.py -- dispute tier reconciliation service data/computation layer.

Provides a pure function that can be called from the router or directly in
tests.  All data access is performed via the real SQLAlchemy models from
`app.models` and the session provided by `app.db.get_session`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute, McpServerRegistry


_TIER_ORDER = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
]
_TIER_INDEX: Dict[Optional[str], int] = {tier: i for i, tier in enumerate(_TIER_ORDER)}
# Unknown / None is considered worse than the worst known tier.
_UNKNOWN_INDEX = len(_TIER_ORDER)


def _tier_to_index(tier: Optional[str]) -> int:
    """Map a tier string (or None) to its ordered index."""
    if tier is None:
        return _UNKNOWN_INDEX
    return _TIER_INDEX.get(tier, _UNKNOWN_INDEX)


def dispute_tier_reconciliation(
    db: Session, *, status: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compute the tier‑reconciliation view.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    status:
        Optional filter on ``McpScoreDispute.status``.  If supplied only disputes
        with this exact status are considered.

    Returns
    -------
    dict
        Mapping with the shape required by the API contract:
        {
            "total": int,
            "by_status": dict[str, int],
            "by_reason_category": dict[str, int],
            "agree_count": int,
            "escalate_count": int,
            "deescalate_count": int,
            "unknown_tier_count": int,
            "stale_count": int,
            "items": list[dict],
        }
    """
    # ------------------------------------------------------------------ #
    # 1️⃣  Load disputes with a left‑join to the server registry.
    # ------------------------------------------------------------------ #
    stmt = (
        select(McpScoreDispute, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpScoreDispute.server_id == McpServerRegistry.server_id,
            isouter=True,
        )
        .order_by(McpScoreDispute.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(McpScoreDispute.status == status)

    rows = db.execute(stmt).all()

    # ------------------------------------------------------------------ #
    # 2️⃣  Walk rows, build items and aggregates.
    # ------------------------------------------------------------------ #
    items: List[Dict[str, Any]] = []
    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    agree = 0
    escal = 0
    deesc = 0
    unknown_tier = 0
    stale = 0

    now = _dt.datetime.utcnow()

    for dispute, server in rows:
        # Basic fields
        dispute_id = dispute.id
        server_id = dispute.server_id
        server_name = getattr(server, "name", None)

        # Tiers
        proposed_tier = dispute.proposed_overall_risk
        current_tier = server.risk_tier if server and server.risk_tier else "UNKNOWN"

        # Indexes for delta calculation
        prop_idx = _tier_to_index(proposed_tier)
        cur_idx = _tier_to_index(current_tier)

        # Delta: positive means escalation, negative means de‑escalation
        delta = prop_idx - cur_idx

        # Age and staleness
        age_days = (now - dispute.created_at).days
        is_stale = age_days > 30

        # Aggregates
        by_status[dispute.status] = by_status.get(dispute.status, 0) + 1
        by_reason[dispute.reason_category] = by_reason.get(
            dispute.reason_category, 0
        ) + 1

        if delta == 0:
            agree += 1
        elif delta > 0:
            escal += 1
        else:
            deesc += 1

        if current_tier == "UNKNOWN":
            unknown_tier += 1

        if is_stale:
            stale += 1

        items.append(
            {
                "dispute_id": dispute_id,
                "server_id": server_id,
                "server_name": server_name,
                "status": dispute.status,
                "reason_category": dispute.reason_category,
                "proposed_tier": proposed_tier,
                "current_tier": current_tier,
                "delta": delta,
                "age_days": age_days,
                "is_stale": is_stale,
            }
        )

    # ------------------------------------------------------------------ #
    # 3️⃣  Assemble final payload.
    # ------------------------------------------------------------------ #
    result: Dict[str, Any] = {
        "total": len(items),
        "by_status": by_status,
        "by_reason_category": by_reason,
        "agree_count": agree,
        "escalate_count": escal,
        "deescalate_count": deesc,
        "unknown_tier_count": unknown_tier,
        "stale_count": stale,
        "items": items,
    }
    return result


# ---------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    # The test creates an in‑memory SQLite DB, injects three disputes and
    # corresponding server rows, runs the function and validates the output.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the Base metadata to create tables in the temporary DB.
    from app.db import Base  # type: ignore

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        # Helper to add rows
        def add_server(server_id: str, name: str, risk_tier: Optional[str]):
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    risk_tier=risk_tier,
                    verdict="UNKNOWN",
                    confidence=0,
                )
            )

        def add_dispute(
            dispute_id: int,
            server_id: str,
            proposed: Optional[str],
            reason: str,
            status: str,
            created_offset_days: int,
        ):
            db.add(
                McpScoreDispute(
                    id=dispute_id,
                    server_id=server_id,
                    proposed_overall_risk=proposed,
                    reason_category=reason,
                    status=status,
                    created_at=_dt.datetime.utcnow()
                    - _dt.timedelta(days=created_offset_days),
                    explanation="test",
                    submitted_by="tester",
                )
            )

        # 1️⃣  Agreeing dispute (same tier)
        add_server("srv1", "Server One", "TRUSTED_GENERAL")
        add_dispute(1, "srv1", "TRUSTED_GENERAL", "CATEGORY_A", "OPEN", 10)

        # 2️⃣  Escalating dispute (higher tier)
        add_server("srv2", "Server Two", "TRUSTED_GENERAL")
        add_dispute(2, "srv2", "ENTERPRISE_CONTROLLED", "CATEGORY_B", "OPEN", 5)

        # 3️⃣  Unknown current tier (NULL risk_tier)
        add_server("srv3", "Server Three", None)
        add_dispute(3, "srv3", "TRUSTED_RESEARCH", "CATEGORY_C", "OPEN", 40)

        db.commit()

        out = dispute_tier_reconciliation(db)

        assert out["total"] == 3, "total mismatch"
        assert out["agree_count"] == 1, "agree count mismatch"
        assert out["escalate_count"] == 1, "escalate count mismatch"
        assert out["unknown_tier_count"] == 1, "unknown tier count mismatch"
        assert len(out["items"]) == 3, "items length mismatch"
        print("PASS")