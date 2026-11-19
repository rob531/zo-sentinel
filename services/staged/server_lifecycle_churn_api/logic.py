from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


COHORT_ORDER = ("NEW", "ACTIVE", "DORMANT", "NEVER_SCANNED")


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def _is_within(value: Any, cutoff: datetime) -> bool:
    timestamp = _as_utc(value)
    return timestamp is not None and timestamp >= cutoff


def _is_older(value: Any, cutoff: datetime) -> bool:
    timestamp = _as_utc(value)
    return timestamp is not None and timestamp < cutoff


def _cohort_for_server(server: McpServerRegistry, cutoff: datetime) -> str:
    if (server.scan_count or 0) == 0:
        return "NEVER_SCANNED"
    if _is_within(server.first_seen, cutoff):
        return "NEW"
    if _is_within(server.last_seen, cutoff):
        return "ACTIVE"
    return "DORMANT"


def get_lifecycle_churn(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return lifecycle cohort counts and trust/tier summaries for the registry."""
    if days < 0:
        raise ValueError("days must be non-negative")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    servers = db.execute(select(McpServerRegistry)).scalars().all()

    grouped: Dict[str, List[McpServerRegistry]] = {name: [] for name in COHORT_ORDER}
    for server in servers:
        grouped[_cohort_for_server(server, cutoff)].append(server)

    cohorts: List[Dict[str, Any]] = []
    for cohort in COHORT_ORDER:
        members = grouped[cohort]
        scores = [
            float(server.trust_score)
            for server in members
            if server.trust_score is not None
        ]
        tier_mix: Dict[str, int] = {}
        for server in members:
            tier = server.risk_tier or "unknown"
            tier_mix[tier] = tier_mix.get(tier, 0) + 1

        cohorts.append(
            {
                "cohort": cohort,
                "count": len(members),
                "avg_trust_score": round(sum(scores) / len(scores), 4)
                if scores
                else None,
                "tier_mix": tier_mix,
            }
        )

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "cohorts": cohorts,
        "total": len(servers),
    }


def lifecycle_churn(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the service noun as the function name."""
    return get_lifecycle_churn(db, days)


if __name__ == "__main__":
    from fastapi import FastAPI, Query
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="new-server",
                    name="new-server",
                    first_seen=datetime.now(timezone.utc) - timedelta(days=2),
                    last_seen=datetime.now(timezone.utc) - timedelta(days=1),
                    last_scanned=datetime.now(timezone.utc) - timedelta(days=1),
                    scan_count=1,
                    risk_tier="low",
                    trust_score=90.0,
                ),
                McpServerRegistry(
                    server_id="active-server",
                    name="active-server",
                    first_seen=datetime.now(timezone.utc) - timedelta(days=60),
                    last_seen=datetime.now(timezone.utc) - timedelta(days=3),
                    last_scanned=datetime.now(timezone.utc) - timedelta(days=3),
                    scan_count=2,
                    risk_tier="medium",
                    trust_score=80.0,
                ),
                McpServerRegistry(
                    server_id="dormant-server",
                    name="dormant-server",
                    first_seen=datetime.now(timezone.utc) - timedelta(days=90),
                    last_seen=datetime.now(timezone.utc) - timedelta(days=60),
                    last_scanned=datetime.now(timezone.utc) - timedelta(days=60),
                    scan_count=3,
                    risk_tier="high",
                    trust_score=40.0,
                ),
                McpServerRegistry(
                    server_id="never-scanned-server",
                    name="never-scanned-server",
                    first_seen=datetime.now(timezone.utc) - timedelta(days=20),
                    last_seen=datetime.now(timezone.utc) - timedelta(days=10),
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="unknown",
                    trust_score=50.0,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/registry/lifecycle/churn")
    def churn(days: int = Query(30, ge=0)):
        return get_lifecycle_churn(next(override_session()), days)

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get(
        "/api/registry/lifecycle/churn", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert sum(cohort["count"] for cohort in payload["cohorts"]) == 4
    counts = {cohort["cohort"]: cohort["count"] for cohort in payload["cohorts"]}
    assert counts["NEW"] == 1
    assert counts["ACTIVE"] == 1
    assert counts["DORMANT"] == 1
    assert counts["NEVER_SCANNED"] == 1
    print("PASS")