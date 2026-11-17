from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import ThreatIntelRef
except ImportError:
    from app.models import ThreatIntelReference as ThreatIntelRef


__all__ = [
    "get_indicator_type_mix",
    "get_indicator_mix",
    "get_threat_intel_indicator_type_mix",
]


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _grouped_counts(session: Session, column: str) -> list[dict[str, Any]]:
    allowed_columns = {"indicator_type", "source"}
    if column not in allowed_columns:
        raise ValueError(f"Unsupported grouping column: {column}")

    sql = text(
        f"""
        SELECT
            COALESCE({column}, 'unknown') AS group_name,
            COUNT(*) AS item_count
        FROM threat_intel_refs
        GROUP BY COALESCE({column}, 'unknown')
        """
    )
    rows = session.execute(sql).mappings().all()
    return [
        {"indicator_type": row["group_name"], "count": int(row["item_count"])}
        if column == "indicator_type"
        else {"source": row["group_name"], "count": int(row["item_count"])}
        for row in rows
    ]


def get_indicator_type_mix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    total = int(
        session.execute(text("SELECT COUNT(*) FROM threat_intel_refs")).scalar_one()
    )

    by_indicator_type = _grouped_counts(session, "indicator_type")
    by_source = _grouped_counts(session, "source")

    by_indicator_type.sort(key=lambda item: (-item["count"], item["indicator_type"]))
    by_source.sort(key=lambda item: (-item["count"], item["source"]))

    aggregator_count = int(
        session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM threat_intel_refs
                WHERE is_aggregator = :is_aggregator
                """
            ),
            {"is_aggregator": True},
        ).scalar_one()
    )

    fetched_values = session.execute(
        text("SELECT fetched_at FROM threat_intel_refs WHERE fetched_at IS NOT NULL")
    ).scalars().all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stale_count = 0
    for fetched_at in fetched_values:
        parsed = _parse_timestamp(fetched_at)
        if parsed is not None and parsed < cutoff:
            stale_count += 1

    return {
        "total": total,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "aggregator_count": aggregator_count,
        "stale_count": stale_count,
    }


def get_indicator_mix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_indicator_type_mix(session=session)


def get_threat_intel_indicator_type_mix(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_indicator_type_mix(session=session)


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(days=8)
    fresh_at = now - timedelta(days=1)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    pulse_id TEXT,
                    pulse_name TEXT,
                    pulse_created TEXT,
                    is_aggregator INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    source_url TEXT,
                    fetched_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO threat_intel_refs (
                    id,
                    indicator_type,
                    indicator_value,
                    pulse_id,
                    pulse_name,
                    pulse_created,
                    is_aggregator,
                    source,
                    source_url,
                    fetched_at
                ) VALUES (
                    :id,
                    :indicator_type,
                    :indicator_value,
                    :pulse_id,
                    :pulse_name,
                    :pulse_created,
                    :is_aggregator,
                    :source,
                    :source_url,
                    :fetched_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.1",
                    "pulse_id": "pulse-1",
                    "pulse_name": "Pulse 1",
                    "pulse_created": fresh_at.isoformat(),
                    "is_aggregator": 1,
                    "source": "source-a",
                    "source_url": "https://example.test/1",
                    "fetched_at": fresh_at.isoformat(),
                },
                {
                    "id": 2,
                    "indicator_type": "ipv4",
                    "indicator_value": "192.0.2.2",
                    "pulse_id": "pulse-2",
                    "pulse_name": "Pulse 2",
                    "pulse_created": fresh_at.isoformat(),
                    "is_aggregator": 0,
                    "source": "source-b",
                    "source_url": "https://example.test/2",
                    "fetched_at": fresh_at.isoformat(),
                },
                {
                    "id": 3,
                    "indicator_type": "domain",
                    "indicator_value": "example.test",
                    "pulse_id": "pulse-3",
                    "pulse_name": "Pulse 3",
                    "pulse_created": fresh_at.isoformat(),
                    "is_aggregator": 0,
                    "source": "source-a",
                    "source_url": "https://example.test/3",
                    "fetched_at": fresh_at.isoformat(),
                },
                {
                    "id": 4,
                    "indicator_type": "domain",
                    "indicator_value": "example.org",
                    "pulse_id": "pulse-4",
                    "pulse_name": "Pulse 4",
                    "pulse_created": stale_at.isoformat(),
                    "is_aggregator": 0,
                    "source": "source-b",
                    "source_url": "https://example.test/4",
                    "fetched_at": stale_at.isoformat(),
                },
                {
                    "id": 5,
                    "indicator_type": "sha256",
                    "indicator_value": "a" * 64,
                    "pulse_id": "pulse-5",
                    "pulse_name": "Pulse 5",
                    "pulse_created": fresh_at.isoformat(),
                    "is_aggregator": 0,
                    "source": "source-a",
                    "source_url": "https://example.test/5",
                    "fetched_at": fresh_at.isoformat(),
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/indicator-mix")
    def indicator_mix_endpoint(result: dict[str, Any] = Depends(get_indicator_type_mix)):
        return result

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/indicator-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert len(payload["by_indicator_type"]) == 3
        assert payload["aggregator_count"] == 1
        assert payload["stale_count"] == 1

    print("PASS")