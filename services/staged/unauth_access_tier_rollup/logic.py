"""Logic for unauthenticated-access exposure tier rollups.

Reads the authoritative application tables through the real SQLAlchemy models
and returns a JSON-serializable payload suitable for the FastAPI router.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess

_TIER_ORDER = ("LOW", "MEDIUM", "HIGH")
_NON_EXPOSED_VALUES = {"none", "false", "no"}


def _is_exposed(value: object) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return bool(normalized) and normalized not in _NON_EXPOSED_VALUES


def _tier_sort_key(tier: str):
    upper = tier.upper()
    for index, prefix in enumerate(_TIER_ORDER):
        if upper == prefix or upper.startswith(f"{prefix}_"):
            return (index, tier)
    return (len(_TIER_ORDER), tier)


def build_unauth_tier_rollup(db: Session, days: int = 30) -> Dict[str, object]:
    """Build the tier and source rollup for unauthenticated-access findings."""
    try:
        normalized_days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("days must be an integer") from exc
    if normalized_days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=normalized_days
    )
    statement = (
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
        .where(McpUnauthAccess.updated_at >= cutoff)
    )
    rows = db.execute(statement).all()

    tier_servers: Dict[str, set] = {}
    tier_exposed: Dict[str, set] = {}
    source_counts: Dict[str, int] = {}
    all_servers: set = set()
    all_exposed: set = set()

    for server_id, unauth_access, source, risk_tier in rows:
        if server_id is None:
            continue

        tier = (risk_tier or "unknown").strip() or "unknown"
        tier_servers.setdefault(tier, set()).add(server_id)
        all_servers.add(server_id)

        if _is_exposed(unauth_access):
            tier_exposed.setdefault(tier, set()).add(server_id)
            all_exposed.add(server_id)

        source_name = (source or "unknown").strip() or "unknown"
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    tiers: List[Dict[str, object]] = []
    for tier in sorted(tier_servers, key=_tier_sort_key):
        server_count = len(tier_servers[tier])
        exposed_count = len(tier_exposed.get(tier, set()))
        tiers.append(
            {
                "risk_tier": tier,
                "servers": server_count,
                "unauth_exposed": exposed_count,
                "exposure_fraction": (
                    round(exposed_count / server_count, 3) if server_count else 0.0
                ),
            }
        )

    sources = [
        {"source": source, "rows": count}
        for source, count in sorted(source_counts.items())
    ]

    return {
        "days": normalized_days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tiers": tiers,
        "sources": sources,
        "totals": {
            "servers": len(all_servers),
            "unauth_exposed": len(all_exposed),
        },
    }


def get_unauth_tier_rollup(db: Session, days: int = 30) -> Dict[str, object]:
    """Return the unauthenticated-access tier rollup payload."""
    return build_unauth_tier_rollup(db, days)


# Compatibility aliases for router/import naming variants.
build_unauth_access_tier_rollup = build_unauth_tier_rollup
get_unauth_access_tier_rollup = build_unauth_tier_rollup
compute_unauth_tier_rollup = build_unauth_tier_rollup
unauth_tier_rollup = build_unauth_tier_rollup


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpUnauthAccess.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="selftest-a",
                    name="selftest-a",
                    risk_tier="LOW",
                    registry_source="selftest",
                ),
                McpServerRegistry(
                    server_id="selftest-b",
                    name="selftest-b",
                    risk_tier="LOW",
                    registry_source="selftest",
                ),
                McpServerRegistry(
                    server_id="selftest-c",
                    name="selftest-c",
                    risk_tier="HIGH",
                    registry_source="selftest",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="selftest-a",
                    url="http://selftest-a",
                    unauth_access="yes",
                    source="scanner",
                ),
                McpUnauthAccess(
                    server_id="selftest-b",
                    url="http://selftest-b",
                    unauth_access="true",
                    source="scanner",
                ),
                McpUnauthAccess(
                    server_id="selftest-c",
                    url="http://selftest-c",
                    unauth_access="none",
                    source="manual",
                ),
            ]
        )
        session.commit()

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/unauth/tier-rollup")
    def tier_rollup(days: int = 30, db: Session = Depends(get_session)):
        return build_unauth_tier_rollup(db, days)

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/unauth/tier-rollup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 3
    assert payload["totals"]["unauth_exposed"] == 2
    assert any(tier["exposure_fraction"] > 0.5 for tier in payload["tiers"])
    print("PASS")


if __name__ == "__main__":
    _self_test()