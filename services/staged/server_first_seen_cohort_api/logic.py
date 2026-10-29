from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class FirstSeenCohort(BaseModel):
    cohort: str = Field(..., description="ISO year-month of first observation")
    total: int = Field(..., ge=0)
    scored: int = Field(..., ge=0)
    coverage_pct: float = Field(..., ge=0.0, le=100.0)


class FirstSeenCohortResponse(BaseModel):
    months: int = Field(..., ge=1)
    cohorts: List[FirstSeenCohort]


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(month_index, 12)
    return value.replace(year=year, month=month_index + 1, day=1)


def _cohort_label(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m")
    return str(value)


def get_first_seen_cohorts(
    months: int = Query(default=6, ge=1, le=60),
    session: Session = Depends(get_session),
) -> FirstSeenCohortResponse:
    """Return first-seen cohorts and the percentage carrying a risk tier."""
    if months < 1:
        raise ValueError("months must be at least 1")

    now = datetime.now(timezone.utc)
    cutoff = _shift_months(_month_start(now.replace(tzinfo=None)), -(months - 1))

    try:
        dialect_name = session.get_bind().dialect.name
    except Exception:
        dialect_name = "sqlite"

    if dialect_name in {"postgresql", "postgres"}:
        cohort_expression = func.date_trunc(
            "month", McpServerRegistry.first_seen
        ).label("cohort")
    else:
        # SQLite is used by the local self-test; PostgreSQL uses date_trunc.
        cohort_expression = func.strftime(
            "%Y-%m", McpServerRegistry.first_seen
        ).label("cohort")

    has_risk_tier = (
        McpServerRegistry.risk_tier.is_not(None)
        & (func.trim(McpServerRegistry.risk_tier) != "")
    )

    statement = (
        select(
            cohort_expression,
            func.count(McpServerRegistry.server_id).label("total"),
            func.sum(
                case((has_risk_tier, 1), else_=0)
            ).label("scored"),
        )
        .where(McpServerRegistry.first_seen >= cutoff)
        .group_by(cohort_expression)
        .order_by(cohort_expression)
    )

    rows = session.execute(statement).all()
    cohorts = []
    for row in rows:
        total = int(row.total or 0)
        scored = int(row.scored or 0)
        cohorts.append(
            FirstSeenCohort(
                cohort=_cohort_label(row.cohort),
                total=total,
                scored=scored,
                coverage_pct=round((scored / total * 100.0) if total else 0.0, 2),
            )
        )

    return FirstSeenCohortResponse(months=months, cohorts=cohorts)


# Backwards-compatible name for callers that use the shorter service name.
get_first_seen_cohort = get_first_seen_cohorts


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_month = _month_start(now)
    previous_month = _shift_months(current_month, -1)

    with __import__("sqlalchemy.orm").orm.Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-1",
                    name="self-test-1",
                    first_seen=current_month.replace(day=2),
                    last_seen=now,
                    risk_tier="high",
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="self-test-2",
                    name="self-test-2",
                    first_seen=current_month.replace(day=3),
                    last_seen=now,
                    risk_tier=None,
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="self-test-3",
                    name="self-test-3",
                    first_seen=previous_month.replace(day=4),
                    last_seen=now,
                    risk_tier="medium",
                    registry_source="test",
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()

    @local_app.get(
        "/api/registry/first-seen-cohorts",
        response_model=FirstSeenCohortResponse,
    )
    def first_seen_cohorts(
        months: int = Query(default=6, ge=1, le=60),
        db: Session = Depends(get_session),
    ) -> FirstSeenCohortResponse:
        return get_first_seen_cohorts(months=months, session=db)

    local_app.dependency_overrides[get_session] = lambda: __import__(
        "sqlalchemy.orm"
    ).orm.Session(engine)

    from fastapi.testclient import TestClient

    response = TestClient(local_app).get(
        "/api/registry/first-seen-cohorts", params={"months": 6}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["months"] == 6
    assert len(payload["cohorts"]) == 2
    assert any(
        cohort["cohort"] == current_month.strftime("%Y-%m")
        and cohort["total"] == 2
        for cohort in payload["cohorts"]
    )
    print("PASS")


if __name__ == "__main__":
    _run_self_test()