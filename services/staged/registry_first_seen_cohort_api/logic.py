from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


class FirstSeenCohort(BaseModel):
    date: str
    total: int
    by_tier: Dict[str, int]
    by_source: Dict[str, int]


class FirstSeenCohortResponse(BaseModel):
    days: int
    cohorts: List[FirstSeenCohort]


def _iso_date(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.date().isoformat()
        except ValueError:
            return text[:10]
    raise TypeError("Unsupported first_seen value")


@router.get("/registry/first-seen-cohort", response_model=FirstSeenCohortResponse)
def get_first_seen_cohort(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> FirstSeenCohortResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())

    rows = (
        session.query(
            McpServerRegistry.first_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
        .filter(
            McpServerRegistry.first_seen >= start,
            McpServerRegistry.first_seen < end,
        )
        .all()
    )

    totals: Dict[str, int] = defaultdict(int)
    tiers: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sources: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for first_seen, risk_tier, registry_source in rows:
        cohort_date = _iso_date(first_seen)
        tier = str(risk_tier) if risk_tier is not None else "unknown"
        source = str(registry_source) if registry_source is not None else "unknown"
        totals[cohort_date] += 1
        tiers[cohort_date][tier] += 1
        sources[cohort_date][source] += 1

    cohorts = [
        FirstSeenCohort(
            date=cohort_date,
            total=totals[cohort_date],
            by_tier=dict(sorted(tiers[cohort_date].items())),
            by_source=dict(sorted(sources[cohort_date].items())),
        )
        for cohort_date in sorted(totals)
    ]

    return FirstSeenCohortResponse(days=days, cohorts=cohorts)


def _self_test() -> None:
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
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    seeds = [
        ("self-test-1", today, "high", "test-source"),
        ("self-test-2", today, "low", "test-source"),
        ("self-test-3", today - timedelta(days=1), "high", "other-source"),
    ]

    with session_factory() as session:
        for server_id, cohort_date, tier, source in seeds:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    first_seen=datetime.combine(cohort_date, datetime.min.time()),
                    risk_tier=tier,
                    registry_source=source,
                )
            )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/first-seen-cohort",
            params={"days": 7},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 7
        assert len(payload["cohorts"]) == 2
        assert sum(cohort["total"] for cohort in payload["cohorts"]) == 3
        assert payload["cohorts"][0]["total"] == 1
        assert payload["cohorts"][1]["total"] == 2
        assert payload["cohorts"][1]["by_tier"]["high"] == 1
        assert payload["cohorts"][1]["by_tier"]["low"] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()