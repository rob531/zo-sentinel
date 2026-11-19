"""services/staged/registry_scan_density_api/logic.py

Data/computation layer for the registry scan‑density endpoint.

Provides a pure function that operates on a SQLAlchemy ``Session`` and
returns aggregated statistics about ``McpServerRegistry`` rows.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def _bucket(scan: int) -> str:
    """Return the histogram bucket label for a given scan count."""
    if scan == 0:
        return "0"
    if scan == 1:
        return "1"
    if 2 <= scan <= 3:
        return "2-3"
    if 4 <= scan <= 9:
        return "4-9"
    return "10+"


def _median(values: List[int]) -> float:
    """Return the median of a list of integers."""
    n = len(values)
    if n == 0:
        return 0.0
    sorted_vals = sorted(values)
    mid = n // 2
    if n % 2:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def scan_density(
    *,
    registry_source: Optional[str] = None,
    db: Session,
) -> Dict[str, object]:
    """
    Compute scan‑density statistics.

    If *registry_source* is ``None`` the result contains a mapping for **all**
    sources present in the table.  When a source is supplied the mapping for
    that source is returned together with a ``top_never_scanned`` list that
    contains up to 20 ``server_id`` values whose ``scan_count`` is zero.

    The returned dictionary has the shape::

        {
            "stats": {
                "<source>": {
                    "server_count": int,
                    "servers_with_scans": int,
                    "never_scanned_count": int,
                    "median_scan_count": float,
                    "histogram": {
                        "0": int,
                        "1": int,
                        "2-3": int,
                        "4-9": int,
                        "10+": int,
                    },
                },
                ...
            },
            "top_never_scanned": [<server_id>, ...]   # only when a source is given
        }
    """
    # ------------------------------------------------------------------ #
    # Gather rows – either all sources or the requested one.
    # ------------------------------------------------------------------ #
    stmt = select(McpServerRegistry)
    if registry_source is not None:
        stmt = stmt.where(McpServerRegistry.registry_source == registry_source)

    rows = db.execute(stmt).scalars().all()

    # ------------------------------------------------------------------ #
    # Organise rows by source.
    # ------------------------------------------------------------------ #
    by_source: Dict[str, List[McpServerRegistry]] = {}
    for r in rows:
        src = r.registry_source or "unknown"
        by_source.setdefault(src, []).append(r)

    # ------------------------------------------------------------------ #
    # Compute per‑source aggregates.
    # ------------------------------------------------------------------ #
    stats: Dict[str, Dict[str, object]] = {}
    for src, items in by_source.items():
        scan_counts = [int(item.scan_count or 0) for item in items]

        histogram = {"0": 0, "1": 0, "2-3": 0, "4-9": 0, "10+": 0}
        for cnt in scan_counts:
            bucket = _bucket(cnt)
            histogram[bucket] += 1

        server_count = len(items)
        servers_with_scans = sum(1 for c in scan_counts if c > 0)
        never_scanned_count = sum(1 for c in scan_counts if c == 0)

        stats[src] = {
            "server_count": server_count,
            "servers_with_scans": servers_with_scans,
            "never_scanned_count": never_scanned_count,
            "median_scan_count": _median(scan_counts),
            "histogram": histogram,
        }

    # ------------------------------------------------------------------ #
    # If a specific source was requested, also return the top never‑scanned
    # server IDs (up to 20, ordered by server_id for determinism).
    # ------------------------------------------------------------------ #
    result: Dict[str, object] = {"stats": stats}
    if registry_source is not None:
        never_scanned_ids = [
            r.server_id
            for r in sorted(by_source.get(registry_source, []), key=lambda x: x.server_id)
            if (r.scan_count or 0) == 0
        ][:20]
        result["top_never_scanned"] = never_scanned_ids

    return result


# ---------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    # The test builds an in‑memory SQLite DB, creates the required table,
    # seeds six rows across two sources and validates the contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------ #
    # Setup in‑memory DB using the real model metadata.
    # ------------------------------------------------------------------ #
    engine = create_engine("sqlite:///:memory:", future=True)
    McpServerRegistry.__table__.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # ------------------------------------------------------------------ #
    # Seed data.
    # ------------------------------------------------------------------ #
    sess = SessionLocal()
    rows = [
        # source "src1" – two never‑scanned, three with scans
        McpServerRegistry(
            server_id="s1",
            name="srv1",
            registry_source="src1",
            url="http://example.com/1",
            description="d1",
            trust_score=0.5,
            verdict="unknown",
            risk_tier="low",
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="s2",
            name="srv2",
            registry_source="src1",
            url="http://example.com/2",
            description="d2",
            trust_score=0.6,
            verdict="unknown",
            risk_tier="low",
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="s3",
            name="srv3",
            registry_source="src1",
            url="http://example.com/3",
            description="d3",
            trust_score=0.7,
            verdict="unknown",
            risk_tier="medium",
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="s4",
            name="srv4",
            registry_source="src1",
            url="http://example.com/4",
            description="d4",
            trust_score=0.8,
            verdict="unknown",
            risk_tier="medium",
            scan_count=2,
        ),
        McpServerRegistry(
            server_id="s5",
            name="srv5",
            registry_source="src1",
            url="http://example.com/5",
            description="d5",
            trust_score=0.9,
            verdict="unknown",
            risk_tier="high",
            scan_count=5,
        ),
        # source "src2" – one row with a large scan count
        McpServerRegistry(
            server_id="s6",
            name="srv6",
            registry_source="src2",
            url="http://example.com/6",
            description="d6",
            trust_score=0.4,
            verdict="unknown",
            risk_tier="high",
            scan_count=20,
        ),
    ]
    sess.add_all(rows)
    sess.commit()

    # ------------------------------------------------------------------ #
    # Run the logic.
    # ------------------------------------------------------------------ #
    overall = scan_density(db=sess)
    src1_stats = overall["stats"]["src1"]
    src2_stats = overall["stats"]["src2"]

    # Validate expectations.
    assert src1_stats["never_scanned_count"] == 2, "src1 never_scanned_count mismatch"
    assert src2_stats["histogram"]["10+"] == 1, "src2 10+ bucket mismatch"

    # Verify top never‑scanned list for src1.
    src1_detail = scan_density(registry_source="src1", db=sess)
    top_ids = src1_detail["top_never_scanned"]
    assert len(top_ids) == 2 and set(top_ids) == {"s1", "s2"}

    print("PASS")