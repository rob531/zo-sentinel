from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_TIER_ORDER = ("low", "medium", "high")


def _tier_sort_key(tier: str) -> tuple[int, str]:
    try:
        return (_TIER_ORDER.index(tier), tier)
    except ValueError:
        return (len(_TIER_ORDER), tier)


def _clean_tier(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    tier = str(value).strip()
    return tier or None


def _clean_source(value: Optional[str]) -> str:
    if value is None:
        return "unknown"
    source = str(value).strip()
    return source or "unknown"


def fetch_tier_distribution(
    db: Session,
    registry_source: str | None = None,
    window_days: int | None = None,
) -> dict:
    """Return the current registry risk-tier composition, optionally by source."""
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
        McpServerRegistry.scan_count,
    )

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    if window_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        statement = statement.where(
            McpServerRegistry.last_seen.is_not(None),
            McpServerRegistry.last_seen >= cutoff,
        )

    rows = db.execute(statement).all()
    total_servers = len(rows)

    tier_counts: dict[str, int] = {}
    tier_trust_scores: dict[str, list[float]] = {}
    tier_scan_counts: dict[str, list[float]] = {}
    source_rows: dict[str, list] = {}

    for row in rows:
        source = _clean_source(row.registry_source)
        source_rows.setdefault(source, []).append(row)

        tier = _clean_tier(row.risk_tier)
        if tier is None:
            continue

        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        if row.trust_score is not None:
            tier_trust_scores.setdefault(tier, []).append(float(row.trust_score))

        if row.scan_count is not None:
            tier_scan_counts.setdefault(tier, []).append(float(row.scan_count))

    unclassified = sum(
        1 for row in rows if _clean_tier(row.risk_tier) is None
    )

    tiers = []
    for tier in sorted(tier_counts, key=_tier_sort_key):
        count = tier_counts[tier]
        trust_values = tier_trust_scores.get(tier, [])
        scan_values = tier_scan_counts.get(tier, [])
        tiers.append(
            {
                "risk_tier": tier,
                "count": count,
                "pct": (count / total_servers * 100.0) if total_servers else 0.0,
                "avg_trust_score": (
                    sum(trust_values) / len(trust_values)
                    if trust_values
                    else None
                ),
                "median_scan_count": (
                    float(median(scan_values)) if scan_values else None
                ),
            }
        )

    by_source = []
    for source in sorted(source_rows):
        source_data = source_rows[source]
        source_total = len(source_data)
        source_tier_counts: dict[str, int] = {}

        for row in source_data:
            tier = _clean_tier(row.risk_tier)
            if tier is None:
                continue
            source_tier_counts[tier] = source_tier_counts.get(tier, 0) + 1

        source_tiers = []
        for tier in sorted(source_tier_counts, key=_tier_sort_key):
            count = source_tier_counts[tier]
            source_tiers.append(
                {
                    "risk_tier": tier,
                    "count": count,
                    "pct": (
                        (count / source_total * 100.0)
                        if source_total
                        else 0.0
                    ),
                }
            )

        by_source.append(
            {
                "registry_source": source,
                "tiers": source_tiers,
                "total": source_total,
            }
        )

    return {
        "total_servers": total_servers,
        "tiers": tiers,
        "by_source": by_source,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "unclassified": unclassified,
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()

    now = datetime.now(timezone.utc)
    seeded = [
        ("server-1", "registry-a", "low", 95.0, 3),
        ("server-2", "registry-a", "medium", 70.0, 5),
        ("server-3", "registry-a", "high", 20.0, 8),
        ("server-4", "registry-b", "low", 90.0, 2),
        ("server-5", "registry-b", "medium", 65.0, 4),
        ("server-6", "registry-b", "high", 30.0, 7),
    ]
    for server_id, source, tier, trust_score, scan_count in seeded:
        db.add(
            McpServerRegistry(
                server_id=server_id,
                name=server_id,
                registry_source=source,
                risk_tier=tier,
                trust_score=trust_score,
                scan_count=scan_count,
                last_seen=now,
            )
        )
    db.commit()

    test_app = FastAPI()

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/tiers/distribution")
    def distribution_endpoint(
        registry_source: str | None = None,
        window_days: int | None = None,
        session: Session = Depends(get_session),
    ):
        return fetch_tier_distribution(
            session,
            registry_source=registry_source,
            window_days=window_days,
        )

    response = TestClient(test_app).get("/api/tiers/distribution")
    assert response.status_code == 200
    body = response.json()
    assert body["total_servers"] == len(seeded)
    assert sum(tier["count"] for tier in body["tiers"]) == len(seeded)
    assert any(tier["count"] == 2 for tier in body["tiers"])
    assert {entry["registry_source"] for entry in body["by_source"]} == {
        "registry-a",
        "registry-b",
    }
    print("PASS")