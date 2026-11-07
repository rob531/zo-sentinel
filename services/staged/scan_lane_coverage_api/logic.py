"""logic.py -- data/computation for the scan lane coverage API.

Provides a single pure function `lane_coverage` that aggregates
`McpServerRegistry` rows into per‑registry‑source statistics and a
staleness breakdown.  The function is deliberately side‑effect free; it
receives a SQLAlchemy Session and returns a plain‑dict that can be
directly serialized by a FastAPI router.

The module also contains a minimal __main__ self‑test that builds an
in‑memory SQLite database, seeds it with four servers across two
registry sources, invokes the function and asserts the contract described
in the task specification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def lane_coverage(db: Session) -> Dict:
    """Aggregate server registry data for the lane‑coverage endpoint.

    Returns a dict with:
        - generated_at: ISO‑8601 timestamp (UTC)
        - lanes: list of per‑registry_source aggregates
        - staleness: list of bucketed last_scanned age counts
    """
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    # ---------- per‑registry_source aggregates ----------
    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count().label("total_servers"),
            func.count().filter(McpServerRegistry.last_scanned != None).label("scanned_ever"),
            func.count().filter(McpServerRegistry.last_scanned == None).label("never_scanned"),
            func.count()
            .filter(McpServerRegistry.last_scanned >= now - timedelta(days=1))
            .label("scanned_last_24h"),
            func.avg(McpServerRegistry.scan_count).label("avg_scan_count"),
        ).group_by(McpServerRegistry.registry_source)
    ).all()

    # ---------- tier counts per source ----------
    tier_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count().label("cnt"),
        ).group_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
    ).all()

    tier_map: Dict[str, Dict[str, int]] = {}
    for src, tier, cnt in tier_rows:
        src_key = src or "unknown"
        tier_key = tier or "unknown"
        tier_map.setdefault(src_key, {})[tier_key] = int(cnt)

    lanes: List[Dict] = []
    for (
        src,
        total,
        ever,
        never,
        last24h,
        avg_cnt,
    ) in source_rows:
        src_key = src or "unknown"
        lanes.append(
            {
                "registry_source": src_key,
                "total_servers": int(total),
                "scanned_ever": int(ever),
                "never_scanned": int(never),
                "scanned_last_24h": int(last24h),
                "avg_scan_count": float(avg_cnt) if avg_cnt is not None else 0.0,
                "tier_counts": tier_map.get(src_key, {}),
            }
        )

    # ---------- staleness buckets ----------
    bucket_case = case(
        (McpServerRegistry.last_scanned == None, "never"),
        (McpServerRegistry.last_scanned >= now - timedelta(days=1), "0-1d"),
        (McpServerRegistry.last_scanned >= now - timedelta(days=7), "1-7d"),
        (McpServerRegistry.last_scanned >= now - timedelta(days=30), "7-30d"),
        else_="30d+",
    ).label("bucket")

    staleness_rows = db.execute(
        select(bucket_case, func.count().label("cnt")).group_by(bucket_case)
    ).all()

    staleness = [
        {"bucket": bucket, "count": int(cnt)} for bucket, cnt in staleness_rows
    ]

    return {
        "generated_at": now.isoformat(),
        "lanes": lanes,
        "staleness": staleness,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession, sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Import Base from the app's db module to create tables
    from app.db import Base  # type: ignore

    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine, class_=OrmSession, expire_on_commit=False)

    # Helper to provide a Session compatible with the production signature
    def get_test_session() -> OrmSession:
        return TestSession()

    # Seed data: four servers across two registry sources
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            registry_source="source-A",
            risk_tier="high",
            scan_count=5,
            last_scanned=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            registry_source="source-A",
            risk_tier="low",
            scan_count=0,
            last_scanned=None,
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="Server Three",
            registry_source="source-B",
            risk_tier="medium",
            scan_count=3,
            last_scanned=now - timedelta(hours=12),
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="Server Four",
            registry_source="source-B",
            risk_tier="high",
            scan_count=2,
            last_scanned=now - timedelta(days=10),
        ),
    ]

    with get_test_session() as sess:
        sess.add_all(servers)
        sess.commit()

        result = lane_coverage(sess)

    # ---- Assertions per the acceptance criteria ----
    assert isinstance(result, dict)
    assert "lanes" in result and isinstance(result["lanes"], list)
    assert len(result["lanes"]) == 2  # two distinct registry_source values

    # Find the lane for source-A and verify never_scanned == 1
    lane_a = next(l for l in result["lanes"] if l["registry_source"] == "source-A")
    assert lane_a["never_scanned"] == 1

    print("PASS")