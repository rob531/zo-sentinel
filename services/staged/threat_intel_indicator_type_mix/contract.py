from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


app = FastAPI(title="Threat Intel Indicator Type Mix Contract")


@app.get("/api/threat-intel/indicator-type-mix")
def get_indicator_type_mix(
    source: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    params: dict[str, str] = {}
    source_filter = ""

    if source is not None:
        source_filter = "WHERE source = :source"
        params["source"] = source

    total_rows = session.execute(
        text(f"SELECT COUNT(*) FROM threat_intel_refs {source_filter}"),
        params,
    ).scalar_one()

    type_rows = session.execute(
        text(
            f"""
            SELECT
                indicator_type,
                COUNT(*) AS rows,
                COUNT(DISTINCT indicator_value) AS distinct_indicators,
                COUNT(DISTINCT pulse_id) AS distinct_pulses,
                MAX(pulse_created) AS newest_pulse_created,
                MAX(fetched_at) AS newest_fetched_at
            FROM threat_intel_refs
            {source_filter}
            GROUP BY indicator_type
            ORDER BY indicator_type
            """
        ),
        params,
    ).mappings()

    types = [
        {
            "indicator_type": row["indicator_type"],
            "rows": int(row["rows"]),
            "distinct_indicators": int(row["distinct_indicators"]),
            "distinct_pulses": int(row["distinct_pulses"]),
            "newest_pulse_created": row["newest_pulse_created"],
            "newest_fetched_at": row["newest_fetched_at"],
        }
        for row in type_rows
    ]

    aggregator_rows = session.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM threat_intel_refs
            WHERE is_aggregator = true
            {f"AND source = :source" if source is not None else ""}
            """
        ),
        params,
    ).scalar_one()

    return {
        "source": source,
        "total_rows": int(total_rows),
        "types": types,
        "aggregator_rows": int(aggregator_rows),
    }


def _create_test_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    is_aggregator BOOLEAN NOT NULL DEFAULT false,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    pulse_created TEXT,
                    fetched_at TEXT
                )
                """
            )
        )
    return engine


def get_test_session() -> Session:
    engine = _create_test_engine()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return factory()


def seed_test_data(session: Session) -> None:
    session.execute(
        text(
            """
            INSERT INTO threat_intel_refs (
                id,
                indicator_type,
                indicator_value,
                pulse_id,
                pulse_name,
                is_aggregator,
                source,
                source_url,
                pulse_created,
                fetched_at
            )
            VALUES
                (
                    1,
                    'ip',
                    '192.0.2.1',
                    'pulse-1',
                    'Example Pulse 1',
                    false,
                    'example-source',
                    'https://example.test/pulse-1',
                    '2024-01-01T10:00:00Z',
                    '2024-01-01T10:05:00Z'
                ),
                (
                    2,
                    'ip',
                    '192.0.2.2',
                    'pulse-2',
                    'Example Pulse 2',
                    true,
                    'example-source',
                    'https://example.test/pulse-2',
                    '2024-01-02T11:00:00Z',
                    '2024-01-02T11:05:00Z'
                ),
                (
                    3,
                    'domain',
                    'one.example.test',
                    'pulse-1',
                    'Example Pulse 1',
                    false,
                    'example-source',
                    'https://example.test/pulse-1',
                    '2024-01-01T10:00:00Z',
                    '2024-01-01T10:05:00Z'
                ),
                (
                    4,
                    'domain',
                    'two.example.test',
                    'pulse-2',
                    'Example Pulse 2',
                    false,
                    'example-source',
                    'https://example.test/pulse-2',
                    '2024-01-02T11:00:00Z',
                    '2024-01-02T11:05:00Z'
                )
            """
        )
    )
    session.commit()


def run_self_test() -> None:
    engine = _create_test_engine()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            with factory() as session:
                seed_test_data(session)

            response = client.get(
                "/api/threat-intel/indicator-type-mix",
                params={"source": "example-source"},
            )

            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["source"] == "example-source"
            assert payload["total_rows"] == 4
            assert len(payload["types"]) == 2
            assert payload["aggregator_rows"] == 1

            by_type = {entry["indicator_type"]: entry for entry in payload["types"]}
            assert set(by_type) == {"domain", "ip"}
            assert by_type["ip"]["rows"] == 2
            assert by_type["domain"]["rows"] == 2
            assert by_type["ip"]["distinct_indicators"] == 2
            assert by_type["domain"]["distinct_indicators"] == 2
            assert by_type["ip"]["distinct_pulses"] == 2
            assert by_type["domain"]["distinct_pulses"] == 2
            assert by_type["ip"]["newest_pulse_created"] is not None
            assert by_type["domain"]["newest_fetched_at"] is not None
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()