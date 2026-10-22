from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from fastapi import Depends, FastAPI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


RISK_TIERS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
    "UNKNOWN",
)
UNKNOWN_RISK_TIER = "UNKNOWN"


def _normalise_risk_tier(value: Any) -> str:
    if value is None:
        return UNKNOWN_RISK_TIER
    tier = str(value).strip()
    return tier if tier in RISK_TIERS[:-1] else UNKNOWN_RISK_TIER


def _normalise_registry_source(value: Any) -> str:
    if value is None:
        return UNKNOWN_RISK_TIER
    source = str(value).strip()
    return source if source else UNKNOWN_RISK_TIER


def _empty_buckets() -> list[dict[str, Any]]:
    return [{"risk_tier": tier, "count": 0} for tier in RISK_TIERS]


def compute_risk_tier_histogram(
    session: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    if days is None:
        days = 30
    days = int(days)
    if days < 1:
        raise ValueError("days must be at least 1")

    columns = (
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.registry_source,
        McpServerRegistry.trust_score,
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
    )
    statement = select(*columns)

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = statement.where(
        or_(
            McpServerRegistry.last_seen.is_(None),
            McpServerRegistry.last_seen >= cutoff,
        )
    )

    rows = session.execute(statement).mappings().all()
    total = len(rows)
    tier_counts: Counter[str] = Counter()
    source_tier_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        tier = _normalise_risk_tier(row["risk_tier"])
        source = _normalise_registry_source(row["registry_source"])
        tier_counts[tier] += 1
        source_tier_counts[source][tier] += 1

    buckets = []
    for tier in RISK_TIERS:
        count = int(tier_counts.get(tier, 0))
        buckets.append(
            {
                "risk_tier": tier,
                "count": count,
                "pct": round((count / total * 100.0), 2) if total else 0.0,
            }
        )

    by_source = []
    for source in sorted(source_tier_counts):
        source_buckets = _empty_buckets()
        for bucket in source_buckets:
            bucket["count"] = int(source_tier_counts[source].get(bucket["risk_tier"], 0))
        by_source.append(
            {
                "registry_source": source,
                "buckets": source_buckets,
            }
        )

    return {
        "total": int(total),
        "buckets": buckets,
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_risk_tier_histogram(
    session: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_risk_tier_histogram(
        session=session,
        registry_source=registry_source,
        days=days,
    )


def get_registry_risk_tier_histogram(
    session: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_risk_tier_histogram(
        session=session,
        registry_source=registry_source,
        days=days,
    )


def build_risk_tier_histogram(
    session: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_risk_tier_histogram(
        session=session,
        registry_source=registry_source,
        days=days,
    )


def get_histogram(
    session: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, Any]:
    return compute_risk_tier_histogram(
        session=session,
        registry_source=registry_source,
        days=days,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    registry_table = Table(
        "mcp_server_registry",
        metadata,
        Column("server_id", String, primary_key=True),
        Column("name", String),
        Column("risk_tier", String),
        Column("registry_source", String),
        Column("trust_score", String),
        Column("last_assessed", DateTime),
        Column("last_seen", DateTime),
    )
    metadata.create_all(engine)

    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = test_session_factory()
    now = datetime.now(timezone.utc)
    test_session.execute(
        insert(registry_table),
        [
            {
                "server_id": "selftest-1",
                "name": "Trusted One",
                "risk_tier": "TRUSTED_GENERAL",
                "registry_source": "selftest",
                "trust_score": "0.95",
                "last_assessed": now,
                "last_seen": now,
            },
            {
                "server_id": "selftest-2",
                "name": "Trusted Two",
                "risk_tier": "TRUSTED_GENERAL",
                "registry_source": "selftest",
                "trust_score": "0.95",
                "last_assessed": now,
                "last_seen": now,
            },
            {
                "server_id": "selftest-3",
                "name": "Caution",
                "risk_tier": "CAUTION_LIMITED",
                "registry_source": "selftest",
                "trust_score": "0.60",
                "last_assessed": now,
                "last_seen": now,
            },
            {
                "server_id": "selftest-4",
                "name": "Unknown",
                "risk_tier": None,
                "registry_source": "selftest",
                "trust_score": "0.40",
                "last_assessed": now,
                "last_seen": now,
            },
        ],
    )
    test_session.commit()

    def override_get_session() -> Iterable[Session]:
        yield test_session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/registry/risk-tier-histogram")
    def risk_tier_histogram(
        registry_source: Optional[str] = None,
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_risk_tier_histogram(
            session=session,
            registry_source=registry_source,
            days=days,
        )

    with TestClient(local_app) as client:
        response = client.get(
            "/api/registry/risk-tier-histogram",
            params={"registry_source": "selftest", "days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        buckets = {bucket["risk_tier"]: bucket["count"] for bucket in payload["buckets"]}
        assert buckets["TRUSTED_GENERAL"] == 2
        assert buckets["UNKNOWN"] == 1

    test_session.close()
    engine.dispose()
    print("PASS")