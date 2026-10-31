from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class FirstSeenCohort(BaseModel):
    month: str
    server_count: int
    risk_tier_counts: dict[str, int] = Field(default_factory=dict)
    median_days_to_first_scan: float | None = None


class FirstSeenCohortResponse(BaseModel):
    cohorts: list[FirstSeenCohort]
    total_servers: int


FirstSeenCohortsResponse = FirstSeenCohortResponse
RegistryFirstSeenCohortResponse = FirstSeenCohortResponse


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _month(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _median_days_to_first_scan(
    first_seen: datetime | None,
    last_seen: datetime | None,
) -> float | None:
    if first_seen is None or last_seen is None:
        return None
    days = (last_seen - first_seen).total_seconds() / 86400.0
    if days < 0:
        return None
    return days


def get_first_seen_cohorts(
    session: Session = Depends(get_session),
) -> FirstSeenCohortResponse:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
        )
    ).all()

    cohorts: dict[str, dict[str, Any]] = {}

    for row in rows:
        first_seen = _utc_datetime(row.first_seen)
        month = _month(first_seen) if first_seen is not None else "unknown"
        bucket = cohorts.setdefault(
            month,
            {
                "month": month,
                "server_count": 0,
                "risk_tier_counts": {},
                "scan_days": [],
            },
        )
        bucket["server_count"] += 1

        risk_tier = row.risk_tier
        tier_key = str(risk_tier).strip() if risk_tier is not None else "unknown"
        if not tier_key:
            tier_key = "unknown"
        bucket["risk_tier_counts"][tier_key] = (
            bucket["risk_tier_counts"].get(tier_key, 0) + 1
        )

        last_seen = _utc_datetime(row.last_seen)
        scan_days = _median_days_to_first_scan(first_seen, last_seen)
        if scan_days is not None:
            bucket["scan_days"].append(scan_days)

    ordered_months = sorted(
        (month for month in cohorts if month != "unknown"),
    )
    if "unknown" in cohorts:
        ordered_months.append("unknown")

    result: list[FirstSeenCohort] = []
    for month in ordered_months:
        bucket = cohorts[month]
        scan_days = bucket["scan_days"]
        result.append(
            FirstSeenCohort(
                month=month,
                server_count=bucket["server_count"],
                risk_tier_counts=bucket["risk_tier_counts"],
                median_days_to_first_scan=(
                    float(median(scan_days)) if scan_days else None
                ),
            )
        )

    return FirstSeenCohortResponse(
        cohorts=result,
        total_servers=len(rows),
    )


get_registry_first_seen_cohorts = get_first_seen_cohorts
get_cohorts = get_first_seen_cohorts
first_seen_cohorts = get_first_seen_cohorts


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    registry_source VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, first_seen, last_seen, risk_tier, verdict, registry_source)
                VALUES
                    ('server-a', '2025-01-03T10:00:00Z', '2025-01-05T10:00:00Z',
                     'HIGH', 'malicious', 'internal'),
                    ('server-b', '2025-02-04T10:00:00Z', '2025-02-07T10:00:00Z',
                     'LOW', 'benign', 'internal'),
                    ('server-c', NULL, NULL, NULL, NULL, NULL)
                """
            )
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/cohorts/first-seen", response_model=FirstSeenCohortResponse)
    def first_seen_endpoint(session: Session = Depends(get_session)):
        return get_first_seen_cohorts(session)

    response = TestClient(that_app).get("/api/registry/cohorts/first-seen")
    assert response.status_code == 200
    payload = response.json()
    months = {cohort["month"] for cohort in payload["cohorts"]}
    assert "2025-01" in months
    assert "2025-02" in months
    assert "unknown" in months
    unknown = next(
        cohort for cohort in payload["cohorts"] if cohort["month"] == "unknown"
    )
    assert unknown["server_count"] == 1
    assert sum(cohort["server_count"] for cohort in payload["cohorts"]) == 3
    assert payload["total_servers"] == 3
    print("PASS")