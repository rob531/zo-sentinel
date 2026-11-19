"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_coverage_summary(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Return registry coverage and risk-tier counts for the selected sources."""
    scored = (
        select(McpLlmAxisScore.server_id.label("server_id"))
        .distinct()
        .subquery()
    )

    query = (
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(distinct(McpServerRegistry.server_id)),
            func.count(distinct(scored.c.server_id)),
        )
        .outerjoin(scored, scored.c.server_id == McpServerRegistry.server_id)
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    )

    if registry_source is not None:
        query = query.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(query).all()

    sources: Dict[str, Dict[str, Any]] = {}
    for source, tier, server_count, scored_count in rows:
        source_key = source if source is not None else "unknown"
        entry = sources.setdefault(
            source_key,
            {
                "registry_source": source_key,
                "servers": 0,
                "scored_servers": 0,
                "unscored_servers": 0,
                "coverage_pct": 0.0,
                "tier_counts": {},
            },
        )
        server_count = int(server_count or 0)
        scored_count = int(scored_count or 0)
        entry["servers"] += server_count
        entry["scored_servers"] += scored_count
        entry["unscored_servers"] += server_count - scored_count
        tier_key = tier if tier is not None else "unknown"
        entry["tier_counts"][tier_key] = (
            entry["tier_counts"].get(tier_key, 0) + server_count
        )

    source_rows = []
    for entry in sources.values():
        servers = entry["servers"]
        entry["coverage_pct"] = (
            round(entry["scored_servers"] * 100.0 / servers, 2)
            if servers
            else 0.0
        )
        source_rows.append(entry)

    source_rows.sort(key=lambda item: item["registry_source"])

    total_servers = sum(item["servers"] for item in source_rows)
    total_scored = sum(item["scored_servers"] for item in source_rows)
    total_unscored = sum(item["unscored_servers"] for item in source_rows)
    total_coverage = (
        round(total_scored * 100.0 / total_servers, 2) if total_servers else 0.0
    )

    return {
        "totals": {
            "servers": total_servers,
            "scored_servers": total_scored,
            "unscored_servers": total_unscored,
            "coverage_pct": total_coverage,
        },
        "sources": source_rows,
    }


def compute_coverage(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility alias for the coverage computation."""
    return get_coverage_summary(db, registry_source)


def get_coverage_data(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility alias for the coverage computation."""
    return get_coverage_summary(db, registry_source)


def get_coverage(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility alias for the coverage computation."""
    return get_coverage_summary(db, registry_source)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        engine, tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__]
    )
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="one",
                    registry_source="alpha",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="two",
                    registry_source="alpha",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="three",
                    registry_source="beta",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="four",
                    registry_source="beta",
                    risk_tier="medium",
                ),
            ]
        )
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="one",
                    axis_name="risk",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="two",
                    axis_name="risk",
                    model_version="test",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="three",
                    axis_name="risk",
                    model_version="test",
                ),
            ]
        )
        db.commit()

        result = get_coverage_summary(db)
        assert result["totals"]["servers"] == 4
        assert result["totals"]["scored_servers"] == 3
        assert len(result["sources"]) == 2
        alpha = next(
            item for item in result["sources"] if item["registry_source"] == "alpha"
        )
        assert alpha["servers"] == 2
        assert alpha["unscored_servers"] == 0
        filtered = get_coverage_summary(db, "beta")
        assert len(filtered["sources"]) == 1
        assert filtered["sources"][0]["servers"] == 2
        assert filtered["sources"][0]["unscored_servers"] == 1
    finally:
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()