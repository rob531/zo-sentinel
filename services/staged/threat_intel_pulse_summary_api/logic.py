from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_pulse_summary(
    db: Session,
    limit: int = 50,
    source: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))

    where_clause = ""
    params: dict[str, Any] = {"limit": limit}
    if source is not None:
        where_clause = "WHERE source = :source"
        params["source"] = source

    query = text(
        f"""
        WITH filtered AS (
            SELECT
                pulse_id,
                pulse_name,
                indicator_type,
                is_aggregator,
                pulse_created,
                fetched_at,
                source
            FROM threat_intel_refs
            {where_clause}
        ),
        pulse_rollup AS (
            SELECT
                pulse_id,
                MIN(pulse_name) AS pulse_name,
                COUNT(*) AS indicator_count,
                MAX(
                    CASE WHEN COALESCE(is_aggregator, FALSE) THEN 1 ELSE 0 END
                ) AS has_aggregator,
                MIN(pulse_created) AS pulse_created,
                MAX(fetched_at) AS last_fetched_at
            FROM filtered
            GROUP BY pulse_id
        ),
        source_counts AS (
            SELECT
                pulse_id,
                source,
                COUNT(*) AS source_count
            FROM filtered
            GROUP BY pulse_id, source
        ),
        ranked_sources AS (
            SELECT
                pulse_id,
                source,
                ROW_NUMBER() OVER (
                    PARTITION BY pulse_id
                    ORDER BY source_count DESC, source ASC
                ) AS source_rank
            FROM source_counts
        ),
        ranked_pulses AS (
            SELECT
                pulse_rollup.*,
                ranked_sources.source AS top_source
            FROM pulse_rollup
            LEFT JOIN ranked_sources
                ON ranked_sources.pulse_id = pulse_rollup.pulse_id
                AND ranked_sources.source_rank = 1
        )
        SELECT
            pulse_id,
            pulse_name,
            indicator_count,
            has_aggregator,
            pulse_created,
            last_fetched_at,
            top_source,
            COUNT(*) OVER () AS total_pulses
        FROM ranked_pulses
        ORDER BY indicator_count DESC, pulse_id ASC
        LIMIT :limit
        """
    )
    rows = db.execute(query, params).mappings().all()

    types_stmt = select(
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.indicator_type,
    ).where(ThreatIntelRef.indicator_type.is_not(None))
    if source is not None:
        types_stmt = types_stmt.where(ThreatIntelRef.source == source)
    types_stmt = types_stmt.group_by(
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.indicator_type,
    )

    indicator_types: defaultdict[Any, set[str]] = defaultdict(set)
    for pulse_id, indicator_type in db.execute(types_stmt).all():
        indicator_types[pulse_id].add(str(indicator_type))

    pulses = [
        {
            "pulse_id": row["pulse_id"],
            "pulse_name": row["pulse_name"],
            "indicator_count": int(row["indicator_count"]),
            "indicator_types": sorted(indicator_types.get(row["pulse_id"], set())),
            "has_aggregator": bool(row["has_aggregator"]),
            "pulse_created": row["pulse_created"],
            "last_fetched_at": row["last_fetched_at"],
            "top_source": row["top_source"],
        }
        for row in rows
    ]

    return {
        "pulses": pulses,
        "total_pulses": int(rows[0]["total_pulses"]) if rows else 0,
    }


get_threat_intel_pulse_summary = get_pulse_summary
get_threat_intel_pulses = get_pulse_summary


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-1",
                pulse_name="Aggregator Pulse",
                pulse_created=now,
                is_aggregator=True,
                source="source-a",
                source_url="https://source-a.example/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-1",
                pulse_name="Aggregator Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="source-a",
                source_url="https://source-a.example/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="url",
                indicator_value="https://example.test/a",
                pulse_id="pulse-1",
                pulse_name="Aggregator Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="source-b",
                source_url="https://source-b.example/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.20",
                pulse_id="pulse-2",
                pulse_name="Second Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="source-b",
                source_url="https://source-b.example/2",
                fetched_at=now,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/pulses")
    def read_pulses(
        limit: int = 50,
        source: str | None = None,
        db: Session = Depends(get_session),
    ):
        return get_pulse_summary(db, limit=limit, source=source)

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["pulses"]) == 2
        assert payload["total_pulses"] == 2
        aggregator = next(
            pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "pulse-1"
        )
        assert aggregator["has_aggregator"] is True
        assert aggregator["indicator_count"] == 3
        assert aggregator["indicator_types"] == ["domain", "ip", "url"]
        assert aggregator["top_source"] == "source-a"

    test_session.close()
    test_engine.dispose()
    print("PASS")