from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorCount(BaseModel):
    indicator_type: str
    count: int


class PulseSummary(BaseModel):
    pulse_id: str
    pulse_name: str
    source: str
    ref_count: int
    latest_pulse_created: str | None = None


class ThreatIntelAggregatorResponse(BaseModel):
    generated_at: str
    total_refs: int
    aggregator_refs: int
    direct_refs: int
    distinct_pulses: int
    indicators: list[IndicatorCount]
    pulses: list[PulseSummary]


def _as_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_aggregator_summary(session: Session) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT is_aggregator, COUNT(*) AS ref_count,
                   COUNT(DISTINCT pulse_id) AS pulse_count
            FROM threat_intel_refs
            GROUP BY is_aggregator
            """
        )
    ).mappings().all()

    total_refs = 0
    aggregator_refs = 0
    direct_refs = 0
    distinct_pulses = 0

    for row in rows:
        ref_count = int(row["ref_count"] or 0)
        total_refs += ref_count
        distinct_pulses += int(row["pulse_count"] or 0)
        if bool(row["is_aggregator"]):
            aggregator_refs += ref_count
        else:
            direct_refs += ref_count

    indicator_rows = session.execute(
        text(
            """
            SELECT indicator_type, COUNT(*) AS ref_count
            FROM threat_intel_refs
            GROUP BY indicator_type
            ORDER BY ref_count DESC, indicator_type
            """
        )
    ).mappings().all()

    indicators = [
        {
            "indicator_type": str(row["indicator_type"] or ""),
            "count": int(row["ref_count"] or 0),
        }
        for row in indicator_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_refs": total_refs,
        "aggregator_refs": aggregator_refs,
        "direct_refs": direct_refs,
        "distinct_pulses": distinct_pulses,
        "indicators": indicators,
    }


def get_direct_pulses(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT pulse_id, pulse_name, source, COUNT(*) AS ref_count,
                   MAX(pulse_created) AS latest_pulse_created
            FROM threat_intel_refs
            WHERE is_aggregator = false
            GROUP BY pulse_id, pulse_name, source
            ORDER BY ref_count DESC
            LIMIT 50
            """
        )
    ).mappings().all()

    return [
        {
            "pulse_id": str(row["pulse_id"] or ""),
            "pulse_name": str(row["pulse_name"] or ""),
            "source": str(row["source"] or ""),
            "ref_count": int(row["ref_count"] or 0),
            "latest_pulse_created": _as_optional_string(row["latest_pulse_created"]),
        }
        for row in rows
    ]


def get_aggregator_breakdown(session: Session) -> dict[str, Any]:
    result = get_aggregator_summary(session)
    result["pulses"] = get_direct_pulses(session)
    return result


def get_aggregator_stats(session: Session) -> dict[str, Any]:
    return get_aggregator_summary(session)


def get_pulse_breakdown(session: Session) -> list[dict[str, Any]]:
    return get_direct_pulses(session)


def get_threat_intel_aggregators(session: Session) -> dict[str, Any]:
    return get_aggregator_breakdown(session)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type VARCHAR,
                    indicator_value VARCHAR,
                    pulse_id VARCHAR,
                    pulse_name VARCHAR,
                    pulse_created DATETIME,
                    is_aggregator BOOLEAN,
                    source VARCHAR,
                    source_url TEXT,
                    fetched_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs
                    (id, indicator_type, indicator_value, pulse_id, pulse_name,
                     pulse_created, is_aggregator, source, source_url, fetched_at)
                VALUES
                    (1, 'ip', '192.0.2.1', 'pulse-a', 'Pulse A',
                     '2024-01-01 00:00:00', 0, 'cert', 'https://example.test/a',
                     '2024-01-01 00:01:00'),
                    (2, 'ip', '192.0.2.2', 'pulse-a', 'Pulse A',
                     '2024-01-01 00:00:00', 0, 'cert', 'https://example.test/b',
                     '2024-01-01 00:02:00'),
                    (3, 'domain', 'example.test', 'pulse-b', 'Pulse B',
                     '2024-01-02 00:00:00', 0, 'cert', 'https://example.test/c',
                     '2024-01-02 00:01:00'),
                    (4, 'domain', 'example.org', 'pulse-b', 'Pulse B',
                     '2024-01-02 00:00:00', 0, 'cert', 'https://example.test/d',
                     '2024-01-02 00:02:00'),
                    (5, 'ip', '198.51.100.1', 'agg-a', 'Aggregator A',
                     '2024-01-03 00:00:00', 1, 'feed', 'https://example.test/e',
                     '2024-01-03 00:01:00'),
                    (6, 'ip', '198.51.100.2', 'agg-b', 'Aggregator B',
                     '2024-01-04 00:00:00', 1, 'feed', 'https://example.test/f',
                     '2024-01-04 00:01:00')
                """
            )
        )

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/aggregators")
    def threat_intel_aggregators(session: Session = Depends(get_session)):
        return get_aggregator_breakdown(session)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/aggregators")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_refs"] == 6
        assert payload["aggregator_refs"] == 2
        assert payload["direct_refs"] == 4
        assert len(payload["pulses"]) == 2
        assert payload["pulses"][0]["ref_count"] >= payload["pulses"][1]["ref_count"]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()