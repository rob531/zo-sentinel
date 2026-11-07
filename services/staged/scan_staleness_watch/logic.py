"""services/staged/scan_staleness_watch/logic.py

Data/computation layer for the *scan‑staleness‑watch* service.

Provides a single pure‑function that operates over a SQLAlchemy Session
and returns a JSON‑serialisable report describing how stale the scans are
for the servers recorded in ``mcp_server_registry``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def scan_staleness_report(db: Session) -> Dict:
    """
    Return a report about scan staleness.

    The report has the shape::

        {
            "total": int,
            "buckets": [
                {"bucket": "never_scanned", "count": int},
                {"bucket": "older_than_30d", "count": int},
                {"bucket": "older_than_7d", "count": int},
                {"bucket": "fresh", "count": int},
            ],
            "stale_pct": float,          # percent of non‑fresh servers
            "oldest_last_scan": str|None, # ISO‑8601 timestamp or None
            "generated_at": str,          # ISO‑8601 timestamp
        }

    * ``never_scanned`` – ``last_scanned`` is NULL.
    * ``older_than_30d`` – last scan > 30 days ago.
    * ``older_than_7d`` – last scan > 7 days ago (but ≤ 30 days).
    * ``fresh`` – last scan ≤ 7 days ago.

    The calculation is performed in Python so it works on any backend
    (PostgreSQL, SQLite, …) without raw SQL.
    """
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    rows = db.execute(select(McpServerRegistry.last_scanned)).scalars().all()

    bucket_counts = {
        "never_scanned": 0,
        "older_than_30d": 0,
        "older_than_7d": 0,
        "fresh": 0,
    }

    non_null_scans: List[datetime] = []

    for ls in rows:
        if ls is None:
            bucket_counts["never_scanned"] += 1
            continue

        # Ensure timezone‑aware datetime for correct delta calculation
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)

        non_null_scans.append(ls)
        delta = now - ls

        if delta > timedelta(days=30):
            bucket_counts["older_than_30d"] += 1
        elif delta > timedelta(days=7):
            bucket_counts["older_than_7d"] += 1
        else:
            bucket_counts["fresh"] += 1

    total = sum(bucket_counts.values())
    fresh_cnt = bucket_counts["fresh"]
    stale_pct = (
        (total - fresh_cnt) / total * 100.0 if total > 0 else 0.0
    )

    oldest_last_scan = (
        min(non_null_scans).isoformat() if non_null_scans else None
    )

    report = {
        "total": total,
        "buckets": [
            {"bucket": k, "count": v} for k, v in bucket_counts.items()
        ],
        "stale_pct": round(stale_pct, 2),
        "oldest_last_scan": oldest_last_scan,
        "generated_at": now.isoformat(),
    }
    return report


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build a minimal FastAPI app so we can use the same dependency pattern
    app = FastAPI()

    # In‑memory SQLite engine – only for this self‑test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    from app.db import Base  # Base is the declarative base used by the models

    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session() -> Session:
        """Yield a fresh session bound to the in‑memory SQLite engine."""
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Apply the override for the duration of the test
    app.dependency_overrides[get_session] = override_get_session

    # Seed the database with four servers covering the required cases
    with SessionLocal() as db:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

        servers = [
            # never scanned
            McpServerRegistry(
                server_id="srv-0",
                name="NeverScanned",
                last_scanned=None,
            ),
            # older than 30 days
            McpServerRegistry(
                server_id="srv-1",
                name="Old30",
                last_scanned=now - timedelta(days=45),
            ),
            # older than 7 days but ≤30 days
            McpServerRegistry(
                server_id="srv-2",
                name="Old10",
                last_scanned=now - timedelta(days=10),
            ),
            # fresh (≤7 days)
            McpServerRegistry(
                server_id="srv-3",
                name="Fresh",
                last_scanned=now - timedelta(days=2),
            ),
        ]

        db.add_all(servers)
        db.commit()

        # Run the report function
        report = scan_staleness_report(db)

    # Assertions required by the acceptance criteria
    try:
        assert report["total"] == 4, "total count mismatch"
        bucket_map = {b["bucket"]: b["count"] for b in report["buckets"]}
        assert bucket_map.get("never_scanned", 0) == 1, "never_scanned count mismatch"
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")