from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_indicators(
    db: Session,
    indicator_type: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return filtered threat-intelligence indicators and aggregate counts."""
    params: dict[str, Any] = {
        "itype": indicator_type or None,
        "src": source or None,
        "lim": max(1, min(int(limit), 1000)),
    }

    where_clause = (
        "(:itype IS NULL OR indicator_type = :itype) "
        "AND (:src IS NULL OR source = :src)"
    )

    rows = db.execute(
        text(
            """
            SELECT id, indicator_type, indicator_value, pulse_id, pulse_name,
                   pulse_created, is_aggregator, source, source_url, fetched_at
            FROM threat_intel_refs
            WHERE (
                (:itype IS NULL OR indicator_type = :itype)
                AND (:src IS NULL OR source = :src)
            )
            ORDER BY fetched_at DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()

    total = db.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM threat_intel_refs
            WHERE {where_clause}
            """
        ),
        params,
    ).scalar_one()

    type_counts = db.execute(
        text(
            f"""
            SELECT indicator_type, COUNT(*) AS count
            FROM threat_intel_refs
            WHERE {where_clause}
              AND indicator_type IS NOT NULL
            GROUP BY indicator_type
            ORDER BY indicator_type
            """
        ),
        params,
    ).mappings().all()

    source_counts = db.execute(
        text(
            f"""
            SELECT source, COUNT(*) AS count
            FROM threat_intel_refs
            WHERE {where_clause}
              AND source IS NOT NULL
            GROUP BY source
            ORDER BY source
            """
        ),
        params,
    ).mappings().all()

    indicators = []
    for row in rows:
        indicators.append(
            {
                "id": int(row["id"]),
                "indicator_type": str(row["indicator_type"]),
                "indicator_value": str(row["indicator_value"]),
                "pulse_id": _as_text(row["pulse_id"]),
                "pulse_name": _as_text(row["pulse_name"]),
                "pulse_created": _as_text(row["pulse_created"]),
                "is_aggregator": bool(row["is_aggregator"]),
                "source": _as_text(row["source"]),
                "source_url": _as_text(row["source_url"]),
                "fetched_at": _as_text(row["fetched_at"]) or "",
            }
        )

    return {
        "indicators": indicators,
        "total": int(total),
        "by_type": {str(row["indicator_type"]): int(row["count"]) for row in type_counts},
        "by_source": {str(row["source"]): int(row["count"]) for row in source_counts},
    }


def get_indicator_library(
    db: Session,
    indicator_type: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return get_indicators(db, indicator_type=indicator_type, source=source, limit=limit)


def get_threat_intel_indicators(
    db: Session,
    indicator_type: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return get_indicators(db, indicator_type=indicator_type, source=source, limit=limit)


def list_indicators(
    db: Session,
    indicator_type: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return get_indicators(db, indicator_type=indicator_type, source=source, limit=limit)


if __name__ == "__main__":
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
                    indicator_type VARCHAR NOT NULL,
                    indicator_value VARCHAR NOT NULL,
                    pulse_id VARCHAR,
                    pulse_name VARCHAR,
                    pulse_created VARCHAR,
                    is_aggregator BOOLEAN NOT NULL DEFAULT 0,
                    source VARCHAR,
                    source_url VARCHAR,
                    fetched_at VARCHAR NOT NULL
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
                    (1, 'ip', '198.51.100.10', 'pulse-1', 'Pulse One',
                     '2024-01-01', 0, 'internal', 'https://example.test/1',
                     '2024-01-03T00:00:00'),
                    (2, 'domain', 'example.test', 'pulse-2', 'Pulse Two',
                     '2024-01-02', 1, 'internal', 'https://example.test/2',
                     '2024-01-02T00:00:00'),
                    (3, 'ip', '203.0.113.20', NULL, NULL,
                     NULL, 0, 'external', 'https://example.test/3',
                     '2024-01-01T00:00:00')
                """
            )
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/indicators")
    def threat_intel_indicators(
        indicator_type: str | None = Query(default=None),
        source: str | None = Query(default=None),
        limit: int = Query(default=100),
        db: Session = Depends(get_session),
    ):
        return get_indicators(
            db,
            indicator_type=indicator_type,
            source=source,
            limit=limit,
        )

    that_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(that_app)

    response = client.get("/api/threat-intel/indicators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["by_type"] == {"domain": 1, "ip": 2}
    assert payload["by_source"] == {"external": 1, "internal": 2}

    filtered = client.get(
        "/api/threat-intel/indicators",
        params={"indicator_type": "ip"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 2
    assert all(
        item["indicator_type"] == "ip"
        for item in filtered_payload["indicators"]
    )

    print("PASS")