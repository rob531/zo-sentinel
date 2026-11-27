"""logic.py -- data/computation for scan_coverage_backlog_api.

Provides a single pure function that aggregates scan coverage metrics
from the authoritative McpServerRegistry table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_scan_coverage_backlog(db: Session) -> Dict[str, Any]:
    """Return scan coverage backlog statistics.

    The result dictionary matches the contract expected by the router:

    {
        "total_servers": int,
        "never_scanned": int,
        "stale_scanned": int,
        "by_source": [
            {
                "registry_source": str,
                "total": int,
                "never_scanned": int,
                "stale": int,
                "coverage_pct": float,
            },
            ...
        ],
        "backlog": [
            {"server_id": str, "first_seen": datetime, "risk_tier": str},
            ...
        ],
    }
    """
    cutoff = datetime.utcnow() - timedelta(days=7)

    never_scanned_cond = (McpServerRegistry.last_scanned.is_(None)) | (
        McpServerRegistry.scan_count == 0
    )
    stale_cond = (McpServerRegistry.last_scanned.is_not(None)) & (
        McpServerRegistry.last_scanned < cutoff
    )

    total = db.scalar(select(func.count()).select_from(McpServerRegistry))
    never_scanned = db.scalar(
        select(func.count()).where(never_scanned_cond).select_from(McpServerRegistry)
    )
    stale_scanned = db.scalar(
        select(func.count()).where(stale_cond).select_from(McpServerRegistry)
    )

    by_source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count().label("total"),
            func.sum(
                case((never_scanned_cond, 1), else_=0)
            ).label("never_scanned"),
            func.sum(case((stale_cond, 1), else_=0)).label("stale"),
        )
        .group_by(McpServerRegistry.registry_source)
        .select_from(McpServerRegistry)
    ).all()

    by_source: List[Dict[str, Any]] = []
    for source, total_src, never_src, stale_src in by_source_rows:
        total_src = int(total_src)
        never_src = int(never_src)
        stale_src = int(stale_src)
        coverage_pct = (
            ((total_src - never_src) / total_src * 100.0) if total_src else 0.0
        )
        by_source.append(
            {
                "registry_source": source,
                "total": total_src,
                "never_scanned": never_src,
                "stale": stale_src,
                "coverage_pct": coverage_pct,
            }
        )

    backlog_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.risk_tier,
        )
        .where(never_scanned_cond)
        .order_by(McpServerRegistry.first_seen)
        .limit(20)
        .select_from(McpServerRegistry)
    ).all()

    backlog = [
        {"server_id": sid, "first_seen": fs, "risk_tier": rt}
        for sid, fs, rt in backlog_rows
    ]

    return {
        "total_servers": int(total or 0),
        "never_scanned": int(never_scanned or 0),
        "stale_scanned": int(stale_scanned or 0),
        "by_source": by_source,
        "backlog": backlog,
    }


# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create tables.
    McpServerRegistry.metadata.create_all(engine)

    now = datetime.utcnow()
    test_data = [
        # source A
        {
            "server_id": "s1",
            "registry_source": "srcA",
            "first_seen": now - timedelta(days=30),
            "last_scanned": None,
            "scan_count": 0,
            "risk_tier": "high",
        },
        {
            "server_id": "s2",
            "registry_source": "srcA",
            "first_seen": now - timedelta(days=20),
            "last_scanned": None,
            "scan_count": 0,
            "risk_tier": "medium",
        },
        {
            "server_id": "s3",
            "registry_source": "srcA",
            "first_seen": now - timedelta(days=15),
            "last_scanned": now - timedelta(days=2),
            "scan_count": 1,
            "risk_tier": "low",
        },
        # source B
        {
            "server_id": "s4",
            "registry_source": "srcB",
            "first_seen": now - timedelta(days=25),
            "last_scanned": now - timedelta(days=8),
            "scan_count": 1,
            "risk_tier": "high",
        },
        {
            "server_id": "s5",
            "registry_source": "srcB",
            "first_seen": now - timedelta(days=5),
            "last_scanned": now - timedelta(days=1),
            "scan_count": 1,
            "risk_tier": "medium",
        },
        {
            "server_id": "s6",
            "registry_source": "srcB",
            "first_seen": now - timedelta(days=12),
            "last_scanned": now - timedelta(days=1),
            "scan_count": 1,
            "risk_tier": "low",
        },
    ]

    with SessionLocal() as session:
        for rec in test_data:
            session.add(McpServerRegistry(**rec))
        session.commit()

        result = get_scan_coverage_backlog(session)

        assert result["never_scanned"] == 2, "expected 2 never‑scanned servers"
        assert result["stale_scanned"] == 1, "expected 1 stale‑scanned server"
        sources = {s["registry_source"] for s in result["by_source"]}
        assert sources == {"srcA", "srcB"}, "both sources must be present"
        for src in result["by_source"]:
            assert 0.0 <= src["coverage_pct"] <= 100.0, "coverage_pct out of bounds"

    print("PASS")