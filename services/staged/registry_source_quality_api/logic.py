from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import bindparam, case, cast, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry


DEFAULT_STALE_DAYS = 30
TRUSTED_SCORE = 0.7
RISKY_TIERS = ("high", "critical")


def _stale_condition(session: Session, stale_days: int):
    stale_days_param = bindparam("stale_days", value=stale_days)

    if session.bind is not None and session.bind.dialect.name == "sqlite":
        cutoff = datetime.utcnow() - timedelta(days=stale_days)
        return McpServerRegistry.last_assessed < bindparam("stale_cutoff", value=cutoff)

    return McpServerRegistry.last_assessed < (
        func.now() - (stale_days_param * text("INTERVAL '1 day'"))
    )


def get_source_quality(
    db: Session,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    """Return source-level registry quality metrics."""
    stale_days = max(0, int(stale_days))
    stale_condition = _stale_condition(db, stale_days)

    statement = (
        select(
            McpServerRegistry.registry_source.label("source"),
            func.count(McpServerRegistry.server_id).label("servers"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
            func.sum(
                case(
                    (McpServerRegistry.trust_score >= TRUSTED_SCORE, 1),
                    else_=0,
                )
            ).label("trusted_count"),
            func.sum(
                case(
                    (McpServerRegistry.risk_tier.in_(RISKY_TIERS), 1),
                    else_=0,
                )
            ).label("risky_count"),
            func.sum(
                case(
                    (stale_condition, 1),
                    else_=0,
                )
            ).label("stale_count"),
            func.max(McpServerRegistry.last_seen).label("last_seen_max"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )

    rows = db.execute(statement).mappings().all()
    sources = []

    for row in rows:
        avg_trust_score = row["avg_trust_score"]
        if avg_trust_score is not None:
            avg_trust_score = float(avg_trust_score)

        sources.append(
            {
                "source": row["source"],
                "servers": int(row["servers"] or 0),
                "avg_trust_score": avg_trust_score,
                "trusted_count": int(row["trusted_count"] or 0),
                "risky_count": int(row["risky_count"] or 0),
                "stale_count": int(row["stale_count"] or 0),
                "last_seen_max": row["last_seen_max"],
            }
        )

    return {
        "sources": sources,
        "total_sources": len(sources),
    }


def get_registry_source_quality(
    db: Session,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    return get_source_quality(db=db, stale_days=stale_days)


def registry_source_quality(
    db: Session,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    return get_source_quality(db=db, stale_days=stale_days)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    db = TestingSession()
    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-recent",
                    registry_source="source-a",
                    trust_score=0.9,
                    risk_tier="low",
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_assessed=now - timedelta(days=1),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="self-test-stale",
                    registry_source="source-b",
                    trust_score=0.5,
                    risk_tier="medium",
                    first_seen=now - timedelta(days=60),
                    last_seen=now - timedelta(days=40),
                    last_assessed=now - timedelta(days=40),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="self-test-null-score",
                    registry_source="source-c",
                    trust_score=None,
                    risk_tier="low",
                    first_seen=now - timedelta(days=5),
                    last_seen=now,
                    last_assessed=now - timedelta(days=2),
                    scan_count=1,
                ),
            ]
        )
        db.commit()

        result = get_source_quality(db, stale_days=30)
        by_source = {item["source"]: item for item in result["sources"]}

        assert result["total_sources"] == 3
        assert by_source["source-c"]["avg_trust_score"] is None
        assert by_source["source-b"]["stale_count"] == 1
        assert by_source["source-a"]["stale_count"] == 0
        assert by_source["source-c"]["stale_count"] == 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")