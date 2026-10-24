from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


@router.get("/threat-intel/source-mix")
def get_source_mix(session: Session = Depends(get_session)) -> dict:
    source_rows = session.execute(
        select(
            ThreatIntelRef.source.label("source"),
            func.count(ThreatIntelRef.id).label("ref_count"),
            func.count(distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_indicators"
            ),
            func.count(distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_count"),
            func.min(ThreatIntelRef.fetched_at).label("first_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    indicator_rows = session.execute(
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("count"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    sources = [
        {
            "source": row.source,
            "ref_count": int(row.ref_count or 0),
            "distinct_indicators": int(row.distinct_indicators or 0),
            "distinct_pulses": int(row.distinct_pulses or 0),
            "aggregator_count": int(row.aggregator_count or 0),
            "first_fetched_at": row.first_fetched_at,
            "last_fetched_at": row.last_fetched_at,
        }
        for row in source_rows
    ]

    return {
        "total_refs": sum(source["ref_count"] for source in sources),
        "sources": sources,
        "indicator_types": [
            {
                "indicator_type": row.indicator_type,
                "count": int(row.count or 0),
            }
            for row in indicator_rows
        ],
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    ThreatIntelRef.__table__.create(test_engine)

    with TestSession() as session:
        for index in range(1, 6):
            session.add(
                ThreatIntelRef(
                    id=index,
                    indicator_type="ip" if index < 4 else "domain",
                    indicator_value=f"indicator-{index}",
                    source="alpha" if index <= 3 else "beta",
                    is_aggregator=index == 1,
                    pulse_id=f"pulse-{(index - 1) % 3 + 1}",
                    pulse_name=f"Pulse {(index - 1) % 3 + 1}",
                    pulse_created=datetime(2025, 1, index, tzinfo=timezone.utc),
                    fetched_at=datetime(2025, 1, index, 1, tzinfo=timezone.utc),
                    source_url=f"https://example.test/{index}",
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/threat-intel/source-mix")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 5
    assert len(payload["sources"]) == 2
    assert sum(source["ref_count"] for source in payload["sources"]) == 5
    assert sum(source["aggregator_count"] for source in payload["sources"]) == 1

    print("PASS")