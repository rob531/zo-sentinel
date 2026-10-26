from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import case, create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class FirstSeenCohort(BaseModel):
    cohort: str
    total: int
    scored: int
    coverage_pct: float


class FirstSeenCohortResponse(BaseModel):
    months: int = Field(ge=1)
    cohorts: list[FirstSeenCohort]


router = APIRouter(prefix="/api")


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, month_index = divmod(month_index, 12)
    return date(year, month_index + 1, 1)


def _cohort_label(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return str(value)[:7]


def _sqlite_date_trunc(part: str, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%d 00:00:00")


@router.get(
    "/registry/first-seen-cohorts",
    response_model=FirstSeenCohortResponse,
)
def first_seen_cohorts(
    months: int = Query(default=6, ge=1),
    db: Session = Depends(get_session),
) -> FirstSeenCohortResponse:
    current_month = _month_start(datetime.now(timezone.utc).date())
    cutoff_month = _shift_month(current_month, -(months - 1))
    cutoff = datetime.combine(cutoff_month, time.min, tzinfo=timezone.utc)

    cohort = func.date_trunc("month", McpServerRegistry.first_seen)
    scored = case(
        (
            McpServerRegistry.risk_tier.is_not(None)
            & (func.trim(McpServerRegistry.risk_tier) != ""),
            1,
        ),
        else_=0,
    )

    rows = db.execute(
        select(
            cohort.label("cohort"),
            func.count().label("total"),
            func.coalesce(func.sum(scored), 0).label("scored"),
        )
        .where(McpServerRegistry.first_seen >= cutoff)
        .group_by(cohort)
        .order_by(cohort)
    ).mappings()

    cohorts = []
    for row in rows:
        total = int(row["total"])
        scored_count = int(row["scored"])
        cohorts.append(
            FirstSeenCohort(
                cohort=_cohort_label(row["cohort"]),
                total=total,
                scored=scored_count,
                coverage_pct=round((scored_count / total) * 100, 2) if total else 0.0,
            )
        )

    return FirstSeenCohortResponse(months=months, cohorts=cohorts)


def _build_app() -> FastAPI:
    that_app = FastAPI()
    that_app.include_router(router)
    return that_app


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_date_trunc(dbapi_connection, _connection_record) -> None:
        dbapi_connection.create_function("date_trunc", 2, _sqlite_date_trunc)

    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_session() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    that_app = _build_app()
    that_app.dependency_overrides[get_session] = override_get_session

    current = _month_start(datetime.now(timezone.utc).date())
    previous = _shift_month(current, -1)
    current_seen = datetime.combine(current, time(hour=12), tzinfo=timezone.utc)
    previous_seen = datetime.combine(previous, time(hour=12), tzinfo=timezone.utc)

    with session_factory() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="contract-scored-previous",
                    name="contract-scored-previous",
                    url="https://example.com/previous-scored",
                    first_seen=previous_seen,
                    last_seen=previous_seen,
                    risk_tier="high",
                    registry_source="contract-seed",
                ),
                McpServerRegistry(
                    server_id="contract-unscored-previous",
                    name="contract-unscored-previous",
                    url="https://example.com/previous-unscored",
                    first_seen=previous_seen,
                    last_seen=previous_seen,
                    risk_tier="",
                    registry_source="contract-seed",
                ),
                McpServerRegistry(
                    server_id="contract-current",
                    name="contract-current",
                    url="https://example.com/current",
                    first_seen=current_seen,
                    last_seen=current_seen,
                    risk_tier="low",
                    registry_source="contract-seed",
                ),
            ]
        )
        db.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/registry/first-seen-cohorts", params={"months": 6})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["months"] == 6
        assert len(payload["cohorts"]) == 2

        cohorts = {item["cohort"]: item for item in payload["cohorts"]}
        previous_cohort = previous.strftime("%Y-%m")
        assert previous_cohort in cohorts
        assert cohorts[previous_cohort]["total"] == 2
        assert cohorts[previous_cohort]["scored"] == 1
        assert cohorts[previous_cohort]["coverage_pct"] == 50.0

    print("PASS")


if __name__ == "__main__":
    main()