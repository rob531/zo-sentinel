"""logic.py -- data/computation for the registry_scan_cadence service.

Provides a single pure function that operates over a SQLAlchemy Session.
The router layer injects the session via Depends(get_session) and returns
the dict directly to the Pydantic response model.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry


def get_registry_scan_cadence(db: Session) -> Dict:
    """Compute scan cadence statistics per registry_source.

    Returns a dict with:
        - sources: list of dicts per registry_source containing:
            registry_source, total, scanned_24h, scanned_7d,
            never_scanned, mean_scan_count
        - totals: dict with overall servers and never_scanned counts
        - generated_at: ISO‑8601 timestamp (UTC)
    """
    now = datetime.utcnow()
    one_day = timedelta(days=1)
    seven_days = timedelta(days=7)

    # Gather raw rows
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
    ).all()

    # Aggregation containers
    per_source: Dict[str, Dict] = {}
    overall_total = 0
    overall_never = 0

    for source, last_scanned, scan_count in rows:
        if source not in per_source:
            per_source[source] = {
                "registry_source": source,
                "total": 0,
                "scanned_24h": 0,
                "scanned_7d": 0,
                "never_scanned": 0,
                "scan_count_sum": 0,
            }

        src = per_source[source]
        src["total"] += 1
        overall_total += 1

        src["scan_count_sum"] += scan_count or 0

        if last_scanned is None:
            src["never_scanned"] += 1
            overall_never += 1
        else:
            delta = now - last_scanned
            if delta <= one_day:
                src["scanned_24h"] += 1
            if delta <= seven_days:
                src["scanned_7d"] += 1

    # Finalize mean calculations and shape output
    sources_list: List[Dict] = []
    for src in per_source.values():
        total = src["total"]
        mean_scan = src["scan_count_sum"] / total if total else 0.0
        sources_list.append(
            {
                "registry_source": src["registry_source"],
                "total": total,
                "scanned_24h": src["scanned_24h"],
                "scanned_7d": src["scanned_7d"],
                "never_scanned": src["never_scanned"],
                "mean_scan_count": mean_scan,
            }
        )

    result = {
        "sources": sources_list,
        "totals": {"servers": overall_total, "never_scanned": overall_never},
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    return result


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Local in‑memory SQLite for the self‑test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a temporary engine and create tables
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    now = datetime.utcnow()

    # Seed 5 rows across 2 registry_source values
    rows = [
        # source_x: 2 never scanned, 1 scanned today
        McpServerRegistry(
            server_id="srv-1",
            name="srv-1",
            registry_source="source_x",
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="srv-2",
            registry_source="source_x",
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="srv-3",
            registry_source="source_x",
            last_scanned=now,
            scan_count=5,
        ),
        # source_y: 2 scanned (3d ago, 60d ago)
        McpServerRegistry(
            server_id="srv-4",
            name="srv-4",
            registry_source="source_y",
            last_scanned=now - timedelta(days=3),
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="srv-5",
            registry_source="source_y",
            last_scanned=now - timedelta(days=60),
            scan_count=2,
        ),
    ]

    db.add_all(rows)
    db.commit()

    # Run the logic
    out = get_registry_scan_cadence(db)

    # Assertions per acceptance criteria
    assert out["totals"]["servers"] == 5
    assert out["totals"]["never_scanned"] == 2
    assert len(out["sources"]) == 2

    # Find the source with never_scanned == 2
    src_x = next(s for s in out["sources"] if s["never_scanned"] == 2)
    assert src_x["scanned_24h"] == 1

    print("PASS")