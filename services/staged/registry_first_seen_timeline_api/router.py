from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_first_seen_timeline


router = APIRouter(prefix="/api", tags=["registry"])


class TimelinePoint(BaseModel):
    date: str
    source: str
    count: int = Field(ge=0)


class FirstSeenTimelineResponse(BaseModel):
    days: int = Field(ge=0)
    total_new: int = Field(ge=0)
    series: list[TimelinePoint]


def _point_value(point: Any, key: str) -> Any:
    if isinstance(point, dict):
        return point.get(key)
    return getattr(point, key, None)


def _normalise_series(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        raw_series = result.get("series", [])
    else:
        raw_series = result or []

    series: list[dict[str, Any]] = []
    for point in raw_series:
        point_date = _point_value(point, "date")
        if isinstance(point_date, (datetime, date)):
            point_date = point_date.isoformat()
        elif point_date is not None:
            point_date = str(point_date)

        source = _point_value(point, "source")
        count = _point_value(point, "count")
        series.append(
            {
                "date": point_date or "",
                "source": str(source or ""),
                "count": int(count or 0),
            }
        )
    return series


@router.get(
    "/registry/first-seen-timeline",
    response_model=FirstSeenTimelineResponse,
)
def first_seen_timeline(
    days: int = Query(default=30, ge=1, le=3650),
    source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> FirstSeenTimelineResponse:
    result = get_first_seen_timeline(session, days=days, source=source)
    series = _normalise_series(result)

    if isinstance(result, dict):
        total_new = result.get("total_new")
        if total_new is None:
            total_new = sum(item["count"] for item in series)
        result_days = result.get("days", days)
    else:
        total_new = sum(item["count"] for item in series)
        result_days = days

    return FirstSeenTimelineResponse(
        days=int(result_days),
        total_new=int(total_new),
        series=[TimelinePoint(**item) for item in series],
    )


def _self_test() -> None:
    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_date_trunc(dbapi_connection: Any, _connection_record: Any) -> None:
        dbapi_connection.create_function(
            "date_trunc",
            2,
            lambda _unit, value: value[:10] if value is not None else None,
        )

    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    today = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    yesterday = today - timedelta(days=1)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-1",
                    registry_source="alpha",
                    first_seen=yesterday,
                    last_seen=yesterday,
                ),
                McpServerRegistry(
                    server_id="self-test-2",
                    registry_source="alpha",
                    first_seen=yesterday,
                    last_seen=yesterday,
                ),
                McpServerRegistry(
                    server_id="self-test-3",
                    registry_source="beta",
                    first_seen=today,
                    last_seen=today,
                ),
            ]
        )
        session.commit()

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/first-seen-timeline",
            params={"days": 2},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_new"] == 3, payload
    assert sum(item["count"] for item in payload["series"]) == 3, payload
    assert len({item["source"] for item in payload["series"]}) == 2, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()