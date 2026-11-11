from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_risk_distribution(
    registry_source: str | None = None,
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    statement = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = session.execute(statement).all()
    total = len(rows)

    tier_counts: Counter[str] = Counter()
    source_tier_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    source_totals: Counter[str] = Counter()
    stale_count = 0
    unassessed_count = 0

    assessed_values = [row.last_assessed for row in rows if row.last_assessed is not None]
    if assessed_values:
        sample = assessed_values[0]
        if sample.tzinfo is None:
            cutoff = datetime.utcnow() - timedelta(days=days)
        else:
            cutoff = datetime.now(sample.tzinfo) - timedelta(days=days)
    else:
        cutoff = datetime.utcnow() - timedelta(days=days)

    for row in rows:
        source = row.registry_source if row.registry_source is not None else "UNKNOWN"
        tier = row.risk_tier if row.risk_tier is not None else "UNKNOWN"

        tier_counts[tier] += 1
        source_totals[source] += 1
        source_tier_counts[source][tier] += 1

        if row.last_assessed is None:
            unassessed_count += 1
        elif row.last_assessed < cutoff:
            stale_count += 1

    by_source = [
        {
            "registry_source": source,
            "total": source_totals[source],
            "by_tier": dict(sorted(source_tier_counts[source].items())),
        }
        for source in sorted(source_totals)
    ]

    return {
        "generated_for_days": days,
        "total": total,
        "by_tier": dict(sorted(tier_counts.items())),
        "by_source": by_source,
        "stale_count": stale_count,
        "unassessed_count": unassessed_count,
    }


def get_risk_distribution(
    registry_source: str | None = None,
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_registry_risk_distribution(
        registry_source=registry_source,
        days=days,
        session=session,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
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
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    test_session = test_session_factory()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="registry-risk-1",
                name="registry-risk-1",
                registry_source="source-a",
                risk_tier="HIGH_RISK",
                trust_score=20,
                verdict="UNSAFE",
                confidence=0.9,
                last_assessed=now - timedelta(days=1),
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="registry-risk-2",
                name="registry-risk-2",
                registry_source="source-a",
                risk_tier="INSUFFICIENT",
                trust_score=50,
                verdict="INSUFFICIENT",
                confidence=0.5,
                last_assessed=now - timedelta(days=40),
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="registry-risk-3",
                name="registry-risk-3",
                registry_source="source-b",
                risk_tier="LOW_RISK",
                trust_score=90,
                verdict="SAFE",
                confidence=0.95,
                last_assessed=None,
                last_seen=now,
            ),
            McpServerRegistry(
                server_id="registry-risk-4",
                name="registry-risk-4",
                registry_source="source-b",
                risk_tier="HIGH_RISK",
                trust_score=15,
                verdict="UNSAFE",
                confidence=0.85,
                last_assessed=now - timedelta(days=2),
                last_seen=now,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()

    @local_app.get("/api/registry/risk-distribution")
    def registry_risk_distribution(
        registry_source: str | None = None,
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_registry_risk_distribution(
            registry_source=registry_source,
            days=days,
            session=session,
        )

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get(
            "/api/registry/risk-distribution",
            params={"registry_source": "source-a", "days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["generated_for_days"] == 30
        assert payload["total"] == 2
        assert payload["by_tier"] == {"HIGH_RISK": 1, "INSUFFICIENT": 1}
        assert payload["by_source"] == [
            {
                "registry_source": "source-a",
                "total": 2,
                "by_tier": {"HIGH_RISK": 1, "INSUFFICIENT": 1},
            }
        ]
        assert payload["stale_count"] == 1
        assert payload["unassessed_count"] == 0

        response = client.get(
            "/api/registry/risk-distribution",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["by_tier"] == {
            "HIGH_RISK": 2,
            "INSUFFICIENT": 1,
            "LOW_RISK": 1,
        }
        assert payload["stale_count"] == 1
        assert payload["unassessed_count"] == 1

    print("PASS")