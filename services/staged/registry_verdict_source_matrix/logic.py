"""Data and computation layer for the registry verdict source matrix."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_verdict_source_matrix(
    db: Session,
    source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return registry counts grouped by ingestion source and matrix tier."""
    source_expr = func.coalesce(McpServerRegistry.registry_source, "UNKNOWN")
    tier_expr = case(
        (McpServerRegistry.risk_tier.is_(None), "UNSCORED"),
        (McpServerRegistry.verdict.is_(None), "NO_VERDICT"),
        else_=McpServerRegistry.risk_tier,
    )

    query = (
        select(
            source_expr.label("source"),
            tier_expr.label("tier"),
            func.count().label("count"),
        )
        .group_by(source_expr, tier_expr)
        .order_by(source_expr)
    )

    if source is not None:
        query = query.where(McpServerRegistry.registry_source == source)

    query = query.limit(max(0, int(limit)))
    rows = db.execute(query).all()

    by_source: Dict[str, list[Dict[str, Any]]] = {}
    for source_name, tier_name, count in rows:
        by_source.setdefault(str(source_name), []).append(
            {
                "tier": str(tier_name),
                "count": int(count),
            }
        )

    sources = []
    for source_name, tiers in by_source.items():
        total = sum(entry["count"] for entry in tiers)
        for entry in tiers:
            entry["pct"] = (
                float(entry["count"] * 100.0 / total) if total else 0.0
            )
        sources.append(
            {
                "source": source_name,
                "tiers": tiers,
                "total": total,
            }
        )

    return {
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_verdict_source_matrix(
    db: Session,
    source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Compatibility alias for the source-matrix query."""
    return get_registry_verdict_source_matrix(db, source=source, limit=limit)


def get_source_matrix(
    db: Session,
    source: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Compatibility alias for the source-matrix query."""
    return get_registry_verdict_source_matrix(db, source=source, limit=limit)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="matrix-1",
                    name="Matrix One",
                    registry_source="source-a",
                    risk_tier="LOW",
                    verdict="ALLOW",
                ),
                McpServerRegistry(
                    server_id="matrix-2",
                    name="Matrix Two",
                    registry_source="source-a",
                    risk_tier="MEDIUM",
                    verdict="REVIEW",
                ),
                McpServerRegistry(
                    server_id="matrix-3",
                    name="Matrix Three",
                    registry_source="source-b",
                    risk_tier="HIGH",
                    verdict="BLOCK",
                ),
                McpServerRegistry(
                    server_id="matrix-4",
                    name="Matrix Four",
                    registry_source="source-b",
                    risk_tier=None,
                    verdict="ALLOW",
                ),
                McpServerRegistry(
                    server_id="matrix-5",
                    name="Matrix Five",
                    registry_source="source-b",
                    risk_tier="LOW",
                    verdict=None,
                ),
            ]
        )
        db.commit()

        result = get_registry_verdict_source_matrix(db)
        names = {item["source"] for item in result["sources"]}
        assert names == {"source-a", "source-b"}
        unscored = [
            tier
            for item in result["sources"]
            for tier in item["tiers"]
            if tier["tier"] == "UNSCORED"
        ]
        assert unscored and unscored[0]["count"] == 1
        assert all(
            tier["count"] >= 0
            and 0 <= tier["pct"] <= 100
            for item in result["sources"]
            for tier in item["tiers"]
        )

    print("PASS")