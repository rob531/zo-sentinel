"""logic.py -- data/computation for the registry_unauth_access_summary service.

Provides a single pure function that aggregates unauthenticated MCP server exposure
information from the real application database via SQLAlchemy ORM models.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpUnauthAccess, McpServerRegistry


def summarize_unauth_access(db: Session, days: int = 30) -> Dict:
    """Summarise unauthenticated MCP server exposure.

    Args:
        db: SQLAlchemy Session obtained via ``Depends(get_session)``.
        days: Look‑back window in days (default 30).

    Returns:
        A dictionary matching the contract specification.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    total_rows_q = select(func.count()).where(McpUnauthAccess.updated_at >= cutoff)
    unauth_rows = db.execute(total_rows_q).scalar_one()

    distinct_servers_q = select(func.count(func.distinct(McpUnauthAccess.server_id))).where(
        McpUnauthAccess.updated_at >= cutoff
    )
    distinct_servers = db.execute(distinct_servers_q).scalar_one()

    # ------------------------------------------------------------------
    # Group by unauth_access
    # ------------------------------------------------------------------
    by_unauth_q = (
        select(
            McpUnauthAccess.unauth_access,
            func.count(func.distinct(McpUnauthAccess.server_id)),
        )
        .where(McpUnauthAccess.updated_at >= cutoff)
        .group_by(McpUnauthAccess.unauth_access)
    )
    by_unauth_rows = db.execute(by_unauth_q).all()
    by_unauth_access: List[Dict] = [
        {"unauth_access": ua, "servers": int(cnt)} for ua, cnt in by_unauth_rows
    ]

    # ------------------------------------------------------------------
    # Group by source
    # ------------------------------------------------------------------
    by_source_q = (
        select(
            McpUnauthAccess.source,
            func.count(func.distinct(McpUnauthAccess.server_id)),
        )
        .where(McpUnauthAccess.updated_at >= cutoff)
        .group_by(McpUnauthAccess.source)
    )
    by_source_rows = db.execute(by_source_q).all()
    by_source: List[Dict] = [
        {"source": src, "servers": int(cnt)} for src, cnt in by_source_rows
    ]

    # ------------------------------------------------------------------
    # Group by risk_tier (join to registry)
    # ------------------------------------------------------------------
    by_risk_q = (
        select(
            McpServerRegistry.risk_tier,
            func.count(func.distinct(McpUnauthAccess.server_id)),
        )
        .join(
            McpUnauthAccess,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
            isouter=False,
        )
        .where(McpUnauthAccess.updated_at >= cutoff)
        .group_by(McpServerRegistry.risk_tier)
    )
    by_risk_rows = db.execute(by_risk_q).all()
    by_risk_tier: List[Dict] = [
        {"risk_tier": tier, "servers": int(cnt)} for tier, cnt in by_risk_rows
    ]

    # ------------------------------------------------------------------
    # Detailed server rows
    # ------------------------------------------------------------------
    servers_q = (
        select(
            McpUnauthAccess.server_id,
            McpServerRegistry.name,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpServerRegistry.risk_tier,
            McpUnauthAccess.url,
        )
        .join(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
            isouter=True,
        )
        .where(McpUnauthAccess.updated_at >= cutoff)
        .order_by(McpUnauthAccess.server_id.desc())
    )
    server_rows = db.execute(servers_q).all()
    servers: List[Dict] = [
        {
            "server_id": sid,
            "name": name,
            "unauth_access": unauth,
            "source": src,
            "risk_tier": tier,
            "url": url,
        }
        for sid, name, unauth, src, tier, url in server_rows
    ]

    return {
        "days": days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "totals": {"servers": int(distinct_servers), "unauth_rows": int(unauth_rows)},
        "by_unauth_access": by_unauth_access,
        "by_source": by_source,
        "by_risk_tier": by_risk_tier,
        "servers": servers,
    }


# ----------------------------------------------------------------------
# __main__ self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import Base to create tables in the temporary SQLite DB
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    now = datetime.utcnow()

    # Seed McpServerRegistry rows
    reg1 = McpServerRegistry(
        server_id="srv-1",
        name="Alpha",
        risk_tier="high",
        verdict="ok",
    )
    reg2 = McpServerRegistry(
        server_id="srv-2",
        name=None,
        risk_tier=None,
        verdict="ok",
    )
    reg3 = McpServerRegistry(
        server_id="srv-3",
        name="Gamma",
        risk_tier="low",
        verdict="ok",
    )
    db.add_all([reg1, reg2, reg3])

    # Seed McpUnauthAccess rows
    ua1 = McpUnauthAccess(
        server_id="srv-1",
        unauth_access="none",
        source="src-A",
        url="http://alpha.example",
        updated_at=now,
    )
    ua2 = McpUnauthAccess(
        server_id="srv-2",
        unauth_access="none",
        source="src-B",
        url=None,
        updated_at=now,
    )
    ua3 = McpUnauthAccess(
        server_id="srv-3",
        unauth_access="partial",
        source="src-A",
        url="http://gamma.example",
        updated_at=now,
    )
    db.add_all([ua1, ua2, ua3])
    db.commit()

    result = summarize_unauth_access(db, days=30)

    # Assertions per acceptance criteria
    assert result["totals"]["servers"] == 3
    none_bucket = next(
        b for b in result["by_unauth_access"] if b["unauth_access"] == "none"
    )
    assert none_bucket["servers"] == 2
    null_risk_bucket = next(
        b for b in result["by_risk_tier"] if b["risk_tier"] is None
    )
    assert null_risk_bucket["servers"] == 1

    print("PASS")