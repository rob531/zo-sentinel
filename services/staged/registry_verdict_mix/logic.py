"""logic.py -- registry verdict-mix computation over the real application data layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _bucket(value: Any) -> str:
    """Return the presentation bucket for a nullable registry value."""
    return str(value) if value is not None else "UNSCORED"


def _percentage(count: int, total: int) -> float:
    if not total:
        return 0.0
    return round((count / total) * 100.0, 2)


def get_verdict_mix(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compute the current verdict and risk-tier composition of the registry.

    The optional axis-score table supplies daily movement buckets.  Registry
    rows remain authoritative for the current population and for verdicts.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(365, days))

    registry_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
    ).all()

    total_servers = len(registry_rows)
    verdict_counts: Dict[str, int] = {}
    risk_tier_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    scored_servers = 0

    for _server_id, verdict, risk_tier, registry_source in registry_rows:
        verdict_bucket = _bucket(verdict)
        verdict_counts[verdict_bucket] = verdict_counts.get(verdict_bucket, 0) + 1

        if verdict is not None:
            scored_servers += 1

        risk_bucket = _bucket(risk_tier)
        risk_tier_counts[risk_bucket] = risk_tier_counts.get(risk_bucket, 0) + 1

        source_bucket = _bucket(registry_source)
        source_counts[source_bucket] = source_counts.get(source_bucket, 0) + 1

    cutoff = datetime.utcnow() - timedelta(days=days)
    daily_rows = db.execute(
        select(
            func.date(McpLlmAxisScore.scored_at).label("bucket_date"),
            func.coalesce(McpServerRegistry.verdict, "UNSCORED").label("verdict"),
            func.count().label("count"),
        )
        .select_from(McpLlmAxisScore)
        .join(McpServerRegistry, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .where(McpLlmAxisScore.scored_at.is_not(None))
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            func.date(McpLlmAxisScore.scored_at),
            func.coalesce(McpServerRegistry.verdict, "UNSCORED"),
        )
        .order_by(func.date(McpLlmAxisScore.scored_at))
    ).all()

    daily_by_date: Dict[str, Dict[str, int]] = {}
    for bucket_date, verdict, count in daily_rows:
        date_key = str(bucket_date)
        daily_by_date.setdefault(date_key, {})[str(verdict)] = int(count)

    daily: List[Dict[str, Any]] = []
    for date_key in sorted(daily_by_date):
        counts = daily_by_date[date_key]
        top_verdict = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        daily.append(
            {
                "date": date_key,
                "count": sum(counts.values()),
                "top_verdict": top_verdict,
            }
        )

    by_verdict = [
        {
            "verdict": verdict,
            "count": count,
            "pct": _percentage(count, total_servers),
        }
        for verdict, count in sorted(
            verdict_counts.items(),
            key=lambda item: (item[0] == "UNSCORED", item[0]),
        )
    ]
    by_risk_tier = [
        {
            "risk_tier": risk_tier,
            "count": count,
            "pct": _percentage(count, total_servers),
        }
        for risk_tier, count in sorted(
            risk_tier_counts.items(),
            key=lambda item: (item[0] == "UNSCORED", item[0]),
        )
    ]
    by_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(
            source_counts.items(),
            key=lambda item: (item[0] == "UNSCORED", item[0]),
        )
    ]

    return {
        "days": days,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "servers": total_servers,
            "scored": scored_servers,
            "unscored": total_servers - scored_servers,
        },
        "by_verdict": by_verdict,
        "by_risk_tier": by_risk_tier,
        "daily": daily,
        "by_source": by_source,
    }


# Compatibility names for routers and consumers that use a verb-oriented
# function name.
compute_verdict_mix = get_verdict_mix
build_verdict_mix = get_verdict_mix


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-safe",
                    name="contract-safe",
                    verdict="safe",
                    risk_tier="low",
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="contract-risky",
                    name="contract-risky",
                    verdict="risky",
                    risk_tier="high",
                    registry_source="contract",
                ),
                McpServerRegistry(
                    server_id="contract-unscored",
                    name="contract-unscored",
                    verdict=None,
                    risk_tier=None,
                    registry_source="contract",
                ),
            ]
        )
        session.commit()

        application = FastAPI()

        @application.get("/api/registry/verdict-mix")
        def verdict_mix_endpoint(days: int = 30, db: Session = Depends(get_session)):
            return get_verdict_mix(db, days)

        def override_get_session():
            yield session

        application.dependency_overrides[get_session] = override_get_session
        response = TestClient(application).get("/api/registry/verdict-mix?days=30")

        assert response.status_code == 200
        payload = response.json()
        assert {entry["verdict"] for entry in payload["by_verdict"]} == {
            "safe",
            "risky",
            "UNSCORED",
        }
        assert sum(entry["count"] for entry in payload["by_risk_tier"]) == payload["totals"]["servers"]

    print("PASS")