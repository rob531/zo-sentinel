from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_first_seen_timeline(
    session: Session,
    days: int = 30,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return newly registered servers grouped by day and registry source."""
    days = max(0, int(days))
    cutoff = datetime.utcnow() - timedelta(days=days)

    day_expression = func.date_trunc("day", McpServerRegistry.first_seen)
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        day_expression = func.date(McpServerRegistry.first_seen)

    statement = (
        select(
            day_expression.label("date"),
            McpServerRegistry.registry_source.label("source"),
            func.count().label("count"),
        )
        .where(McpServerRegistry.first_seen >= cutoff)
        .group_by(day_expression, McpServerRegistry.registry_source)
        .order_by(day_expression, McpServerRegistry.registry_source)
    )

    if source is not None:
        statement = statement.where(McpServerRegistry.registry_source == source)

    rows = session.execute(statement).all()

    series: List[Dict[str, Any]] = []
    for row in rows:
        value = row.date
        if isinstance(value, datetime):
            date_value = value.date().isoformat()
        elif hasattr(value, "isoformat"):
            date_value = value.isoformat()
        else:
            date_value = str(value)

        series.append(
            {
                "date": date_value,
                "source": row.source,
                "count": int(row.count),
            }
        )

    return {
        "days": days,
        "total_new": sum(item["count"] for item in series),
        "series": series,
    }


def aggregate_first_seen_timeline(
    session: Session,
    days: int = 30,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return get_first_seen_timeline(session, days=days, source=source)


def get_registry_first_seen_timeline(
    session: Session,
    days: int = 30,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return get_first_seen_timeline(session, days=days, source=source)


def run(
    session: Session,
    days: int = 30,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    return get_first_seen_timeline(session, days=days, source=source)


__all__ = [
    "aggregate_first_seen_timeline",
    "get_first_seen_timeline",
    "get_registry_first_seen_timeline",
    "run",
]


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-1",
                    registry_source="REGISTRY_SOURCE_A",
                    first_seen=now - timedelta(days=1, hours=2),
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="self-test-2",
                    registry_source="REGISTRY_SOURCE_A",
                    first_seen=now - timedelta(days=1, hours=1),
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="self-test-3",
                    registry_source="REGISTRY_SOURCE_B",
                    first_seen=now - timedelta(hours=1),
                    last_seen=now,
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/first-seen-timeline")
    def first_seen_timeline(
        days: int = 30,
        source: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        return get_first_seen_timeline(session, days=days, source=source)

    response = TestClient(test_app).get(
        "/api/registry/first-seen-timeline",
        params={"days": 30},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_new"] == 3
    assert sum(item["count"] for item in payload["series"]) == 3
    assert {item["source"] for item in payload["series"]} == {
        "REGISTRY_SOURCE_A",
        "REGISTRY_SOURCE_B",
    }
    print("PASS")