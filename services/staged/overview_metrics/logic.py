import datetime
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_overview_metrics(
    session: Session = Depends(get_session),
) -> dict:
    """Return aggregated overview metrics for MCP servers."""
    # Total servers
    total = session.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Servers by risk tier
    tier_rows = (
        session.query(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_tier = {tier: count for tier, count in tier_rows if tier is not None}

    # Servers by verdict
    verdict_rows = (
        session.query(McpServerRegistry.verdict, func.count())
        .group_by(McpServerRegistry.verdict)
        .all()
    )
    by_verdict = {verdict: count for verdict, count in verdict_rows if verdict is not None}

    # Growth windows
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    last_7d = (
        session.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.first_seen >= seven_days_ago)
        .scalar()
        or 0
    )
    last_30d = (
        session.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.first_seen >= thirty_days_ago)
        .scalar()
        or 0
    )

    return {
        "total": total,
        "by_tier": by_tier,
        "by_verdict": by_verdict,
        "growth": {"last_7d": last_7d, "last_30d": last_30d},
        "as_of": now.isoformat(),
    }


if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite database.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base

    # Create a temporary SQLite engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables.
    Base.metadata.create_all(bind=engine)

    # Seed test data.
    now = datetime.now(timezone.utc)
    test_servers = [
        McpServerRegistry(
            server_id="s1",
            risk_tier="high",
            verdict="malicious",
            first_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="s2",
            risk_tier="medium",
            verdict="benign",
            first_seen=now - timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="s3",
            risk_tier="low",
            verdict="unknown",
            first_seen=now - timedelta(days=20),
        ),
        McpServerRegistry(
            server_id="s4",
            risk_tier="high",
            verdict="malicious",
            first_seen=now - timedelta(days=40),
        ),
        McpServerRegistry(
            server_id="s5",
            risk_tier="medium",
            verdict="benign",
            first_seen=now - timedelta(days=5),
        ),
    ]

    with SessionLocal() as session:
        session.add_all(test_servers)
        session.commit()

        result = get_overview_metrics(session)

        assert result["total"] == 5, "total count mismatch"
        assert isinstance(result["by_tier"], dict) and result["by_tier"], "by_tier empty"
        assert isinstance(result["by_verdict"], dict) and result["by_verdict"], "by_verdict empty"
        assert result["growth"]["last_7d"] >= 2, "growth last_7d too low"

    print("PASS")