from collections.abc import Generator

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import ThreatIntelRef


def get_aggregators(
    session: Session,
    limit: int = 25,
) -> dict:
    """Return the leaderboard of aggregator indicators."""
    aggregate_sql = text(
        """
        SELECT
            indicator_type,
            indicator_value,
            COUNT(DISTINCT pulse_id) AS pulse_count,
            MAX(pulse_created) AS latest_pulse_created
        FROM threat_intel_refs
        WHERE is_aggregator = :is_aggregator
        GROUP BY indicator_type, indicator_value
        ORDER BY
            pulse_count DESC,
            latest_pulse_created DESC NULLS LAST,
            indicator_type ASC,
            indicator_value ASC
        LIMIT :limit
        """
    )
    aggregate_rows = session.execute(
        aggregate_sql,
        {"is_aggregator": True, "limit": limit},
    ).mappings().all()

    sources_sql = text(
        """
        SELECT DISTINCT source
        FROM threat_intel_refs
        WHERE is_aggregator = :is_aggregator
          AND indicator_type = :indicator_type
          AND indicator_value = :indicator_value
          AND source IS NOT NULL
        ORDER BY source ASC
        """
    )

    aggregators = []
    for row in aggregate_rows:
        source_rows = session.execute(
            sources_sql,
            {
                "is_aggregator": True,
                "indicator_type": row["indicator_type"],
                "indicator_value": row["indicator_value"],
            },
        ).scalars().all()
        aggregators.append(
            {
                "indicator_type": row["indicator_type"],
                "indicator_value": row["indicator_value"],
                "pulse_count": int(row["pulse_count"] or 0),
                "latest_pulse_created": row["latest_pulse_created"],
                "sources": list(source_rows),
            }
        )

    indicator_total_sql = text(
        """
        SELECT COUNT(*)
        FROM (
            SELECT indicator_type, indicator_value
            FROM threat_intel_refs
            WHERE is_aggregator = :is_aggregator
            GROUP BY indicator_type, indicator_value
        ) AS distinct_indicators
        """
    )
    pulse_total_sql = text(
        """
        SELECT COUNT(DISTINCT pulse_id)
        FROM threat_intel_refs
        WHERE is_aggregator = :is_aggregator
        """
    )

    indicator_total = session.execute(
        indicator_total_sql,
        {"is_aggregator": True},
    ).scalar_one()
    pulse_total = session.execute(
        pulse_total_sql,
        {"is_aggregator": True},
    ).scalar_one()

    return {
        "aggregators": aggregators,
        "totals": {
            "indicators": int(indicator_total or 0),
            "pulses": int(pulse_total or 0),
        },
    }


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine: Engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/aggregators")
    def aggregators_endpoint(
        limit: int = 25,
        session: Session = Depends(get_session),
    ) -> dict:
        return get_aggregators(session=session, limit=limit)

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="aggregator.example",
                    pulse_id="pulse-1",
                    pulse_name="First Pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="source-a",
                    source_url="https://source-a.example/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="aggregator.example",
                    pulse_id="pulse-2",
                    pulse_name="Second Pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="source-b",
                    source_url="https://source-b.example/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="ordinary.example",
                    pulse_id="pulse-3",
                    pulse_name="Third Pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="source-c",
                    source_url="https://source-c.example/3",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="other.example",
                    pulse_id="pulse-4",
                    pulse_name="Fourth Pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="source-d",
                    source_url="https://source-d.example/4",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/threat-intel/aggregators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["aggregators"][0]["pulse_count"] == 2
    assert payload["totals"]["indicators"] == 1
    assert payload["totals"]["pulses"] == 2
    print("PASS")