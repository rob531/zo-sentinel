from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class RegistryCohort(BaseModel):
    month: str
    new_servers: int
    rescored_servers: int


class RegistryCohortTotals(BaseModel):
    servers: int
    distinct_cohorts: int


class RegistryCohortIngestResponse(BaseModel):
    cohorts: list[RegistryCohort]
    totals: RegistryCohortTotals


def _month_value(value: object) -> str:
    if isinstance(value, str):
        return value[:7]
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    return str(value)[:7]


@router.get(
    "/registry/cohorts/ingest",
    response_model=RegistryCohortIngestResponse,
)
def get_registry_cohort_ingest(
    session: Session = Depends(get_session),
) -> RegistryCohortIngestResponse:
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"

    if dialect_name == "postgresql":
        month_expression = func.date_trunc(
            "month", McpServerRegistry.first_seen
        )
    else:
        month_expression = func.strftime(
            "%Y-%m", McpServerRegistry.first_seen
        )

    statement = (
        select(
            month_expression.label("month"),
            func.count(McpServerRegistry.server_id).label("new_servers"),
            func.coalesce(
                func.sum(
                    case(
                        (McpServerRegistry.scan_count > 1, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("rescored_servers"),
        )
        .where(McpServerRegistry.first_seen.is_not(None))
        .group_by(month_expression)
        .order_by(month_expression)
    )

    rows = session.execute(statement).mappings().all()

    cohorts = [
        RegistryCohort(
            month=_month_value(row["month"]),
            new_servers=int(row["new_servers"] or 0),
            rescored_servers=int(row["rescored_servers"] or 0),
        )
        for row in rows
    ]

    return RegistryCohortIngestResponse(
        cohorts=cohorts,
        totals=RegistryCohortTotals(
            servers=sum(cohort.new_servers for cohort in cohorts),
            distinct_cohorts=len(cohorts),
        ),
    )


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session_factory = sessionmaker(bind=engine, autoflush=False)

    seeded_rows = [
        {
            "server_id": "server-1",
            "name": "Server One",
            "url": "https://server-one.example",
            "first_seen": datetime(2024, 1, 3, 12, 0, 0),
            "scan_count": 1,
        },
        {
            "server_id": "server-2",
            "name": "Server Two",
            "url": "https://server-two.example",
            "first_seen": datetime(2024, 1, 12, 12, 0, 0),
            "scan_count": 2,
        },
        {
            "server_id": "server-3",
            "name": "Server Three",
            "url": "https://server-three.example",
            "first_seen": datetime(2024, 1, 22, 12, 0, 0),
            "scan_count": 3,
        },
        {
            "server_id": "server-4",
            "name": "Server Four",
            "url": "https://server-four.example",
            "first_seen": datetime(2024, 2, 4, 12, 0, 0),
            "scan_count": 1,
        },
        {
            "server_id": "server-5",
            "name": "Server Five",
            "url": "https://server-five.example",
            "first_seen": datetime(2024, 2, 18, 12, 0, 0),
            "scan_count": 4,
        },
    ]

    with testing_session_factory.begin() as session:
        session.execute(McpServerRegistry.__table__.insert(), seeded_rows)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with testing_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/cohorts/ingest")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["distinct_cohorts"] == 2
        assert sum(item["new_servers"] for item in payload["cohorts"]) == 5
        january = next(
            item for item in payload["cohorts"] if item["month"] == "2024-01"
        )
        assert january["rescored_servers"] == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()