"""Registry trust divergence computation over the real application registry."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _normalise_tier(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    tier = str(value).strip().upper()
    return tier or "UNKNOWN"


def _normalise_source(value: Any) -> str:
    if value is None:
        return "unknown"
    source = str(value).strip()
    return source or "unknown"


def _trust_score(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(score):
        return None
    return score


def _expected_tier(score: float) -> str:
    if score >= 80:
        return "LOW"
    if score >= 60:
        return "MEDIUM"
    if score >= 40:
        return "HIGH"
    return "CRITICAL"


def compute_registry_trust_divergence(db: Session) -> Dict[str, Any]:
    """Compute trust-score/tier divergence and registry group summaries."""
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).all()

    source_groups: Dict[str, Dict[str, Any]] = {}
    tier_groups: Dict[str, Dict[str, Any]] = {}
    total_servers = 0
    unscored = 0
    never_assessed = 0

    for registry_source, raw_score, raw_tier, last_assessed in rows:
        total_servers += 1
        source = _normalise_source(registry_source)
        tier = _normalise_tier(raw_tier)
        score = _trust_score(raw_score)

        if score is None:
            unscored += 1
        if last_assessed is None:
            never_assessed += 1

        source_group = source_groups.setdefault(
            source,
            {
                "registry_source": source,
                "count": 0,
                "scores": [],
                "diverged": 0,
            },
        )
        source_group["count"] += 1
        if score is not None:
            source_group["scores"].append(score)
            if tier != _expected_tier(score):
                source_group["diverged"] += 1

        tier_group = tier_groups.setdefault(
            tier,
            {"risk_tier": tier, "count": 0, "scores": []},
        )
        tier_group["count"] += 1
        if score is not None:
            tier_group["scores"].append(score)

    by_source: List[Dict[str, Any]] = []
    for source in sorted(source_groups):
        group = source_groups[source]
        scores = group["scores"]
        by_source.append(
            {
                "registry_source": source,
                "count": group["count"],
                "avg_trust_score": sum(scores) / len(scores) if scores else None,
                "min_trust_score": min(scores) if scores else None,
                "max_trust_score": max(scores) if scores else None,
                "diverged": group["diverged"],
            }
        )

    by_tier: List[Dict[str, Any]] = []
    for tier in sorted(tier_groups):
        group = tier_groups[tier]
        scores = group["scores"]
        by_tier.append(
            {
                "risk_tier": tier,
                "count": group["count"],
                "avg_trust_score": sum(scores) / len(scores) if scores else None,
            }
        )

    return {
        "total_servers": total_servers,
        "unscored": unscored,
        "never_assessed": never_assessed,
        "by_source": by_source,
        "by_tier": by_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_registry_trust_divergence(db: Session) -> Dict[str, Any]:
    return compute_registry_trust_divergence(db)


def compute_trust_divergence(db: Session) -> Dict[str, Any]:
    return compute_registry_trust_divergence(db)


def get_trust_divergence(db: Session) -> Dict[str, Any]:
    return compute_registry_trust_divergence(db)


if __name__ == "__main__":
    from datetime import datetime as _datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="trust-92",
                    name="Low risk server",
                    trust_score=92,
                    risk_tier="LOW",
                    last_assessed=_datetime(2025, 1, 1),
                ),
                McpServerRegistry(
                    server_id="trust-15",
                    name="Critical server",
                    trust_score=15,
                    risk_tier="CRITICAL",
                    last_assessed=_datetime(2025, 1, 1),
                ),
                McpServerRegistry(
                    server_id="trust-null",
                    name="Unscored server",
                    trust_score=None,
                    risk_tier=None,
                    last_assessed=None,
                ),
            ]
        )
        session.commit()

        result = compute_registry_trust_divergence(session)
        assert result["total_servers"] == 3
        assert result["unscored"] == 1
        assert result["never_assessed"] == 1
        assert len(result["by_tier"]) >= 2

    engine.dispose()
    print("PASS")