from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_cohort_tier_aging(
    age_days: int = 30,
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    active_session = session if session is not None else db
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
    )
    rows = active_session.execute(statement).all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
    cohorts: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        first_seen = row.first_seen
        if first_seen is None:
            cohort = "unknown"
            stale = False
        else:
            cohort = first_seen.strftime("%Y-%m")
            stale = _naive_utc(first_seen) < cutoff.replace(tzinfo=None)

        if cohort not in cohorts:
            cohorts[cohort] = {
                "cohort": cohort,
                "servers": 0,
                "tiers": {},
                "scan_count_total": 0,
                "never_assessed": 0,
                "stale_over_window": 0,
            }

        cohort_data = cohorts[cohort]
        cohort_data["servers"] += 1
        cohort_data["scan_count_total"] += row.scan_count or 0
        cohort_data["never_assessed"] += int(row.last_assessed is None)
        cohort_data["stale_over_window"] += int(stale)

        tier = row.risk_tier if row.risk_tier is not None else "unknown"
        cohort_data["tiers"][tier] = cohort_data["tiers"].get(tier, 0) + 1

    result: List[Dict[str, Any]] = []
    for cohort in sorted(cohorts):
        cohort_data = cohorts[cohort]
        servers = cohort_data["servers"]
        result.append(
            {
                "cohort": cohort_data["cohort"],
                "servers": servers,
                "tiers": cohort_data["tiers"],
                "mean_scan_count": float(cohort_data["scan_count_total"] / servers),
                "never_assessed": cohort_data["never_assessed"],
                "stale_over_window": cohort_data["stale_over_window"],
            }
        )

    return {
        "cohorts": result,
        "totals": {
            "servers": len(rows),
            "cohorts": len(result),
        },
    }


get_registry_cohort_tier_aging = get_cohort_tier_aging
registry_cohort_tier_aging = get_cohort_tier_aging
get_registry_cohort_aging = get_cohort_tier_aging


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
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = SessionLocal()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="cohort-test-1",
                name="Cohort Test 1",
                first_seen=now - timedelta(days=45),
                last_seen=now,
                last_assessed=now,
                scan_count=3,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="cohort-test-2",
                name="Cohort Test 2",
                first_seen=now - timedelta(days=40),
                last_seen=now,
                last_assessed=None,
                scan_count=0,
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="cohort-test-3",
                name="Cohort Test 3",
                first_seen=now - timedelta(days=75),
                last_seen=now,
                last_assessed=now,
                scan_count=6,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="cohort-test-4",
                name="Cohort Test 4",
                first_seen=now - timedelta(days=70),
                last_seen=now,
                last_assessed=now,
                scan_count=1,
                risk_tier="low",
            ),
        ]
    )
    test_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/cohort-tier-aging")
    def cohort_tier_aging_endpoint(
        age_days: int = 30,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_cohort_tier_aging(age_days=age_days, db=db)

    def override_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    response = TestClient(that_app).get(
        "/api/registry/cohort-tier-aging",
        params={"age_days": 30},
    )
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["cohorts"]) == 2
    assert payload["totals"]["servers"] == 4
    assert sum(item["never_assessed"] for item in payload["cohorts"]) == 1
    assert all(isinstance(item["mean_scan_count"], float) for item in payload["cohorts"])
    assert all(item["mean_scan_count"] >= 0 for item in payload["cohorts"])
    print("PASS")