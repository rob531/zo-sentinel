import os
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import Integer, case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter()


@router.get("/threat-intel/pulse-ranking")
def get_pulse_ranking(
    limit: int = Query(default=100, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    aggregator_flag = case(
        (ThreatIntelRef.is_aggregator.is_(True), 1),
        else_=0,
    )

    statement = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label(
                "distinct_indicator_types"
            ),
            func.min(ThreatIntelRef.pulse_created).label("first_seen"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
            func.max(aggregator_flag).label("is_aggregator"),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
        .limit(limit)
    )

    rows = session.execute(statement).mappings().all()

    pulses = [
        {
            "pulse_id": row["pulse_id"],
            "pulse_name": row["pulse_name"],
            "indicator_count": int(row["indicator_count"]),
            "distinct_indicator_types": int(row["distinct_indicator_types"]),
            "first_seen": row["first_seen"],
            "last_fetched": row["last_fetched"],
            "is_aggregator": bool(row["is_aggregator"]),
        }
        for row in rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "limit": limit,
        "pulses": pulses,
    }


app = FastAPI(title="Threat Intel Pulse Ranking API")
app.include_router(router, prefix="/api")


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    app.dependency_overrides[get_session] = override_get_session

    columns = ThreatIntelRef.__table__.columns
    column_values: list[dict[str, Any]] = [
        {
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "pulse_id": "pulse-one",
            "pulse_name": "Primary Pulse",
            "is_aggregator": True,
            "source": "test-source",
            "source_url": "https://example.test/one",
        },
        {
            "indicator_type": "domain",
            "indicator_value": "example.test",
            "pulse_id": "pulse-one",
            "pulse_name": "Primary Pulse",
            "is_aggregator": True,
            "source": "test-source",
            "source_url": "https://example.test/two",
        },
        {
            "indicator_type": "ip",
            "indicator_value": "192.0.2.2",
            "pulse_id": "pulse-one",
            "pulse_name": "Primary Pulse",
            "is_aggregator": True,
            "source": "test-source",
            "source_url": "https://example.test/three",
        },
        {
            "indicator_type": "hash",
            "indicator_value": "abc123",
            "pulse_id": "pulse-two",
            "pulse_name": "Secondary Pulse",
            "is_aggregator": False,
            "source": "test-source",
            "source_url": "https://example.test/four",
        },
    ]

    with TestSession() as test_session:
        for values in column_values:
            attributes = dict(values)
            if "pulse_created" in columns:
                column_type = columns["pulse_created"].type
                if isinstance(column_type, type(columns["pulse_created"].type)) and hasattr(
                    column_type, "python_type"
                ):
                    try:
                        if column_type.python_type is datetime:
                            attributes["pulse_created"] = datetime(2024, 1, 1, 12, 0, 0)
                        elif column_type.python_type is date:
                            attributes["pulse_created"] = date(2024, 1, 1)
                        else:
                            attributes["pulse_created"] = "2024-01-01T12:00:00"
                    except (AttributeError, TypeError):
                        attributes["pulse_created"] = "2024-01-01T12:00:00"
            if "fetched_at" in columns:
                column_type = columns["fetched_at"].type
                if hasattr(column_type, "python_type"):
                    try:
                        if column_type.python_type is datetime:
                            attributes["fetched_at"] = datetime(2024, 1, 2, 12, 0, 0)
                        elif column_type.python_type is date:
                            attributes["fetched_at"] = date(2024, 1, 2)
                        else:
                            attributes["fetched_at"] = "2024-01-02T12:00:00"
                    except (AttributeError, TypeError):
                        attributes["fetched_at"] = "2024-01-02T12:00:00"
            test_session.add(ThreatIntelRef(**attributes))
        test_session.commit()

    with TestClient(app) as client:
        response = client.get("/api/threat-intel/pulse-ranking", params={"limit": 10})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pulses"][0]["indicator_count"] == 3
        assert payload["pulses"][0]["distinct_indicator_types"] == 2
        assert len(payload["pulses"]) == 2

    print("PASS")