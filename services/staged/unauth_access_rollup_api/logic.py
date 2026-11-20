"""logic.py -- unauth_access_rollup_api data/computation layer.

Provides a single pure function that aggregates unauthenticated access data
joined with server registry information. Mirrors the exemplar doctrine:
imports the real data layer (app.db / app.models) and performs ORM queries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpUnauthAccess, McpServerRegistry


def get_unauth_access_rollup(db: Session) -> Dict[str, object]:
    """Aggregate unauthenticated access information.

    Returns a mapping with:
        - total: total number of rows in mcp_unauth_access
        - by_unauth_access: counts per unauth_access label
        - by_source: counts per source label
        - by_registry_source: counts per registry_source (joined)
        - by_risk_tier: counts per risk_tier (joined)
        - stale_over_30d: rows whose updated_at is >30 days old
    """
    # total rows
    total = db.execute(select(func.count()).select_from(McpUnauthAccess)).scalar_one()

    # counts by unauth_access
    rows = db.execute(
        select(McpUnauthAccess.unauth_access, func.count())
        .group_by(McpUnauthAccess.unauth_access)
    ).all()
    by_unauth_access: Dict[str, int] = {
        (label or "unknown"): int(cnt) for label, cnt in rows
    }

    # counts by source
    rows = db.execute(
        select(McpUnauthAccess.source, func.count())
        .group_by(McpUnauthAccess.source)
    ).all()
    by_source: Dict[str, int] = {
        (label or "unknown"): int(cnt) for label, cnt in rows
    }

    # counts by registry_source (left join)
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count(),
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .group_by(McpServerRegistry.registry_source)
    ).all()
    by_registry_source: Dict[str, int] = {
        (label or "unknown"): int(cnt) for label, cnt in rows
    }

    # counts by risk_tier (left join)
    rows = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(),
        )
        .select_from(McpUnauthAccess)
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    by_risk_tier: Dict[str, int] = {
        (label or "unknown"): int(cnt) for label, cnt in rows
    }

    # stale rows (>30 days)
    cutoff = datetime.utcnow() - timedelta(days=30)
    stale_over_30d = db.execute(
        select(func.count())
        .select_from(McpUnauthAccess)
        .where(McpUnauthAccess.updated_at < cutoff)
    ).scalar_one()

    return {
        "total": int(total),
        "by_unauth_access": by_unauth_access,
        "by_source": by_source,
        "by_registry_source": by_registry_source,
        "by_risk_tier": by_risk_tier,
        "stale_over_30d": int(stale_over_30d),
    }


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for self‑test
    engine = create_engine("sqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create tables
    from app.db import Base  # Base is the declarative base used by app.models

    Base.metadata.create_all(engine)

    # Seed data
    sess = SessionLocal()
    try:
        # Servers
        srv1 = McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            registry_source="source-a",
            risk_tier="high",
            url="https://example.com/1",
        )
        srv2 = McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            registry_source="source-b",
            risk_tier="medium",
            url="https://example.com/2",
        )
        srv3 = McpServerRegistry(
            server_id="srv-3",
            name="Server Three",
            registry_source="source-a",
            risk_tier="low",
            url="https://example.com/3",
        )
        sess.add_all([srv1, srv2, srv3])

        # Unauth access rows
        ua1 = McpUnauthAccess(
            server_id="srv-1",
            url="https://example.com/1",
            unauth_access="none",
            source="scan-1",
            updated_at=datetime.utcnow(),
        )
        ua2 = McpUnauthAccess(
            server_id="srv-2",
            url="https://example.com/2",
            unauth_access="api_key",
            source="scan-2",
            updated_at=datetime.utcnow(),
        )
        ua3 = McpUnauthAccess(
            server_id="srv-3",
            url="https://example.com/3",
            unauth_access="oauth",
            source="scan-1",
            updated_at=datetime.utcnow(),
        )
        sess.add_all([ua1, ua2, ua3])
        sess.commit()

        result = get_unauth_access_rollup(sess)

        assert result["total"] == 3
        assert result["by_unauth_access"] == {"none": 1, "api_key": 1, "oauth": 1}
        assert result["by_source"] == {"scan-1": 2, "scan-2": 1}
        assert result["stale_over_30d"] == 0
        print("PASS")
    finally:
        sess.close()