from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_indicator_type_breakdown(db: Session = Depends(get_session)) -> Dict[str, Any]:
    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("count"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_indicators"
            ),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.indicator_type.asc(),
        )
    ).mappings().all()

    types: List[Dict[str, Any]] = [
        {
            "indicator_type": row["indicator_type"],
            "count": int(row["count"]),
            "distinct_indicators": int(row["distinct_indicators"]),
            "distinct_pulses": int(row["distinct_pulses"]),
            "last_fetched_at": row["last_fetched_at"],
        }
        for row in rows
    ]

    return {
        "total_refs": sum(item["count"] for item in types),
        "types": types,
    }


def get_indicator_type_stats(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_indicator_type_breakdown(db)


def get_indicator_types(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_indicator_type_breakdown(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)

    with Session(engine) as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/1",
                    fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.11",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/2",
                    fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/3",
                    fetched_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()

        @test_app.get("/api/threat-intel/indicator-types")
        def indicator_types(db: Session = Depends(get_session)):
            return get_indicator_type_breakdown(db)

        test_app.dependency_overrides[get_session] = lambda: session

        response = TestClient(test_app).get("/api/threat-intel/indicator-types")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["types"]) == 2
        ip_row = next(
            item for item in payload["types"] if item["indicator_type"] == "ip"
        )
        assert ip_row["count"] == 2
        assert ip_row["distinct_indicators"] == 2
        assert payload["total_refs"] == 3

    print("PASS")