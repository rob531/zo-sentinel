from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


SEVERITIES = ("critical", "high", "medium", "low", "unknown")


class MonthlySeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    unknown: int


class MonthlyFeedCount(BaseModel):
    feed: str
    count: int


class MonthlyAdvisoryCount(BaseModel):
    month: str
    total: int
    by_severity: MonthlySeverityCounts
    by_feed: List[MonthlyFeedCount]


class MonthlyAdvisoryResponse(BaseModel):
    months: List[MonthlyAdvisoryCount]
    total_advisories: int


def _month_start(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - (months - 1)
    year, month_index = divmod(month_index, 12)
    return datetime(year, month_index + 1, 1)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timedelta(0).tzinfo).replace(tzinfo=None)
    return value


def _published_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _as_naive_utc(value)
    if isinstance(value, str):
        try:
            return _as_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


router = APIRouter(prefix="/api")


@router.get(
    "/vuln/advisories/monthly",
    response_model=MonthlyAdvisoryResponse,
)
def monthly_vuln_advisories(
    months: int = Query(default=12, ge=1, le=120),
    db: Session = Depends(get_session),
) -> MonthlyAdvisoryResponse:
    now = datetime.utcnow()
    start = _month_start(now, months)

    rows = db.execute(
        select(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
        ).where(
            VulnAdvisory.published_at.is_not(None),
            VulnAdvisory.published_at >= start,
            VulnAdvisory.published_at <= now,
        )
    ).all()

    grouped = {}
    for feed, severity, published_at in rows:
        published = _published_datetime(published_at)
        if published is None or published < start or published > now:
            continue

        month = published.strftime("%Y-%m")
        normalized_severity = str(severity or "unknown").strip().lower()
        if normalized_severity not in SEVERITIES:
            normalized_severity = "unknown"

        if month not in grouped:
            grouped[month] = {
                "total": 0,
                "by_severity": {key: 0 for key in SEVERITIES},
                "by_feed": Counter(),
            }

        grouped[month]["total"] += 1
        grouped[month]["by_severity"][normalized_severity] += 1
        grouped[month]["by_feed"][str(feed) if feed is not None else "unknown"] += 1

    response_months = []
    for month in sorted(grouped):
        values = grouped[month]
        response_months.append(
            MonthlyAdvisoryCount(
                month=month,
                total=values["total"],
                by_severity=MonthlySeverityCounts(**values["by_severity"]),
                by_feed=[
                    MonthlyFeedCount(feed=feed, count=count)
                    for feed, count in sorted(values["by_feed"].items())
                ],
            )
        )

    return MonthlyAdvisoryResponse(
        months=response_months,
        total_advisories=sum(item.total for item in response_months),
    )


app = FastAPI(title="vuln_advisory_monthly_publish_api")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    VulnAdvisory.__table__.create(test_engine)

    now = datetime.utcnow()
    current_month = datetime(now.year, now.month, 1)
    previous_month = (
        current_month.replace(day=1) - timedelta(days=1)
    ).replace(day=1)

    test_session = TestSession()
    test_session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="osv",
                severity="critical",
                ecosystem="pypi",
                package="example-one",
                published_at=current_month.replace(day=2),
                fetched_at=current_month.replace(day=2),
                source_url="https://example.test/1",
            ),
            VulnAdvisory(
                id=2,
                feed="osv",
                severity="HIGH",
                ecosystem="npm",
                package="example-two",
                published_at=current_month.replace(day=3),
                fetched_at=current_month.replace(day=3),
                source_url="https://example.test/2",
            ),
            VulnAdvisory(
                id=3,
                feed="github",
                severity="medium",
                ecosystem="pypi",
                package="example-three",
                published_at=current_month.replace(day=4),
                fetched_at=current_month.replace(day=4),
                source_url="https://example.test/3",
            ),
            VulnAdvisory(
                id=4,
                feed="github",
                severity="low",
                ecosystem="npm",
                package="example-four",
                published_at=previous_month.replace(day=2),
                fetched_at=previous_month.replace(day=2),
                source_url="https://example.test/4",
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get(
            "/api/vuln/advisories/monthly",
            params={"months": 2},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["months"]) == 2
        three_advisory_month = next(
            item for item in payload["months"] if item["total"] == 3
        )
        assert three_advisory_month["by_severity"]["critical"] == 1

    print("PASS")