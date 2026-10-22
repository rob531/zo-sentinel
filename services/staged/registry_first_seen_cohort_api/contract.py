from datetime import date, datetime, time, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class FirstSeenCohort(BaseModel):
    date: str
    total: int
    by_tier: Dict[str, int]
    by_source: Dict[str, int]


class FirstSeenCohortResponse(BaseModel):
    days: int
    cohorts: List[FirstSeenCohort]


router = APIRouter(prefix="/api")


def get_first_seen_cohort(
    days: int,
    session: Session,
) -> FirstSeenCohortResponse:
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days - 1)
    range_start = datetime.combine(start_date, time.min)
    range_end = datetime.combine(end_date + timedelta(days=1), time.min)

    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
        .filter(
            McpServerRegistry.first_seen >= range_start,
            McpServerRegistry.first_seen < range_end,
        )
        .order_by(McpServerRegistry.first_seen)
        .all()
    )

    cohorts: Dict[str, Dict[str, object]] = {}
    for _server_id, first_seen, risk_tier, registry_source in rows:
        if first_seen is None:
            continue
        cohort_date = first_seen.date().isoformat()
        cohort = cohorts.setdefault(
            cohort_date,
            {
                "date": cohort_date,
                "total": 0,
                "by_tier": {},
                "by_source": {},
            },
        )
        cohort["total"] = int(cohort["total"]) + 1

        tier_counts = cohort["by_tier"]
        source_counts = cohort["by_source"]
        assert isinstance(tier_counts, dict)
        assert isinstance(source_counts, dict)

        tier = risk_tier or "unknown"
        source = registry_source or "unknown"
        tier_counts[tier] = int(tier_counts.get(tier, 0)) + 1
        source_counts[source] = int(source_counts.get(source, 0)) + 1

    return FirstSeenCohortResponse(
        days=days,
        cohorts=[
            FirstSeenCohort(
                date=cohort_date,
                total=int(cohorts[cohort_date]["total"]),
                by_tier={
                    key: value
                    for key, value in sorted(cohorts[cohort_date]["by_tier"].items())
                },
                by_source={
                    key: value
                    for key, value in sorted(cohorts[cohort_date]["by_source"].items())
                },
            )
            for cohort_date in sorted(cohorts)
        ],
    )


@router.get(
    "/registry/first-seen-cohort",
    response_model=FirstSeenCohortResponse,
)
def first_seen_cohort(
    days: int = Query(..., ge=1, le=365),
    session: Session = Depends(get_session),
) -> FirstSeenCohortResponse:
    return get_first_seen_cohort(days=days, session=session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="contract-server-1",
                    first_seen=yesterday.replace(hour=9, minute=0, second=0, microsecond=0),
                    last_seen=now,
                    risk_tier="high",
                    registry_source="github",
                ),
                McpServerRegistry(
                    server_id="contract-server-2",
                    first_seen=now.replace(hour=8, minute=0, second=0, microsecond=0),
                    last_seen=now,
                    risk_tier="high",
                    registry_source="github",
                ),
                McpServerRegistry(
                    server_id="contract-server-3",
                    first_seen=now.replace(hour=10, minute=0, second=0, microsecond=0),
                    last_seen=now,
                    risk_tier="low",
                    registry_source="manual",
                ),
            ]
        )
        db.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/registry/first-seen-cohort", params={"days": 2})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 2
        assert len(payload["cohorts"]) == 2
        totals = {cohort["date"]: cohort["total"] for cohort in payload["cohorts"]}
        assert totals == {
            yesterday.date().isoformat(): 1,
            now.date().isoformat(): 2,
        }

    print("PASS")