from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import summarize_threat_intel_aggregators as _summarize_threat_intel_aggregators
except ImportError:
    _summarize_threat_intel_aggregators = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class ThreatIntelAggregatorCoverageResponse(BaseModel):
    total: int
    aggregator_refs: int
    non_aggregator_refs: int
    distinct_pulses: int
    by_indicator_type: List[Dict[str, Any]] = Field(default_factory=list)
    by_source: List[Dict[str, Any]] = Field(default_factory=list)
    stale_count: int
    stale_days: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_aggregator(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return False


def _summarize_from_database(session: Session, stale_days: int) -> Dict[str, Any]:
    result = session.execute(
        text(
            """
            SELECT
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
            FROM threat_intel_refs
            """
        )
    )
    rows = result.mappings().all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    indicator_counts: Counter[Any] = Counter()
    source_counts: Counter[Any] = Counter()
    pulse_names = set()
    aggregator_refs = 0
    stale_count = 0

    for row in rows:
        indicator_type = row["indicator_type"]
        source = row["source"]
        pulse_name = row["pulse_name"]

        indicator_counts[indicator_type] += 1
        source_counts[source] += 1

        if pulse_name is not None:
            pulse_names.add(pulse_name)

        if _is_aggregator(row["is_aggregator"]):
            aggregator_refs += 1

        fetched_at = _parse_datetime(row["fetched_at"])
        if fetched_at is not None and _as_utc(fetched_at) < cutoff:
            stale_count += 1

    total = len(rows)
    by_indicator_type = [
        {"indicator_type": indicator_type, "count": count}
        for indicator_type, count in sorted(
            indicator_counts.items(),
            key=lambda item: (-item[1], "" if item[0] is None else str(item[0])),
        )
    ]
    by_source = [
        {"source": source, "count": count}
        for source, count in sorted(
            source_counts.items(),
            key=lambda item: (-item[1], "" if item[0] is None else str(item[0])),
        )
    ]

    return {
        "total": total,
        "aggregator_refs": aggregator_refs,
        "non_aggregator_refs": total - aggregator_refs,
        "distinct_pulses": len(pulse_names),
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "stale_count": stale_count,
        "stale_days": stale_days,
    }


def _normalize_summary(summary: Any, stale_days: int) -> Dict[str, Any]:
    if isinstance(summary, BaseModel):
        if hasattr(summary, "model_dump"):
            data = summary.model_dump()
        else:
            data = summary.dict()
    elif isinstance(summary, dict):
        data = dict(summary)
    else:
        raise TypeError("Threat-intel summary must be a mapping or pydantic model")

    data["total"] = int(data["total"])
    data["aggregator_refs"] = int(data["aggregator_refs"])
    data["non_aggregator_refs"] = int(data["non_aggregator_refs"])
    data["distinct_pulses"] = int(data["distinct_pulses"])
    data["by_indicator_type"] = list(data.get("by_indicator_type") or [])
    data["by_source"] = list(data.get("by_source") or [])
    data["stale_count"] = int(data["stale_count"])
    data["stale_days"] = stale_days
    return data


@router.get(
    "/threat_intel/aggregators",
    response_model=ThreatIntelAggregatorCoverageResponse,
    summary="Summarize threat-intelligence reference coverage",
)
def get_threat_intel_aggregator_coverage(
    stale_days: int = Query(
        default=30,
        ge=0,
        description="Number of days before fetched_at that makes a reference stale",
    ),
    session: Session = Depends(get_session),
) -> ThreatIntelAggregatorCoverageResponse:
    if _summarize_threat_intel_aggregators is not None:
        summary = _summarize_threat_intel_aggregators(session, stale_days)
    else:
        summary = _summarize_from_database(session, stale_days)

    return ThreatIntelAggregatorCoverageResponse(
        **_normalize_summary(summary, stale_days)
    )


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE threat_intel_refs (
                    id INTEGER PRIMARY KEY,
                    indicator_type VARCHAR,
                    indicator_value VARCHAR,
                    pulse_id INTEGER,
                    pulse_name VARCHAR,
                    pulse_created TIMESTAMP,
                    is_aggregator BOOLEAN,
                    source VARCHAR,
                    source_url VARCHAR,
                    fetched_at TIMESTAMP
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
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
                    "indicator_type": "domain",
                    "indicator_value": "alpha.example",
                    "pulse_id": 1,
                    "pulse_name": "aggregator-pulse",
                    "pulse_created": now,
                    "is_aggregator": True,
                    "source": "alpha",
                    "source_url": "https://alpha.example/a",
                    "fetched_at": now,
                },
                {
                    "id": 2,
                    "indicator_type": "domain",
                    "indicator_value": "beta.example",
                    "pulse_id": 1,
                    "pulse_name": "aggregator-pulse",
                    "pulse_created": now,
                    "is_aggregator": True,
                    "source": "alpha",
                    "source_url": "https://alpha.example/b",
                    "fetched_at": now,
                },
                {
                    "id": 3,
                    "indicator_type": "ip",
                    "indicator_value": "192.0.2.1",
                    "pulse_id": 1,
                    "pulse_name": "aggregator-pulse",
                    "pulse_created": now,
                    "is_aggregator": True,
                    "source": "beta",
                    "source_url": "https://beta.example/a",
                    "fetched_at": now,
                },
                {
                    "id": 4,
                    "indicator_type": "ip",
                    "indicator_value": "192.0.2.2",
                    "pulse_id": 2,
                    "pulse_name": "regular-pulse",
                    "pulse_created": now,
                    "is_aggregator": False,
                    "source": "beta",
                    "source_url": "https://beta.example/b",
                    "fetched_at": now,
                },
                {
                    "id": 5,
                    "indicator_type": "domain",
                    "indicator_value": "gamma.example",
                    "pulse_id": 2,
                    "pulse_name": "regular-pulse",
                    "pulse_created": now,
                    "is_aggregator": False,
                    "source": "beta",
                    "source_url": "https://beta.example/c",
                    "fetched_at": now,
                },
                {
                    "id": 6,
                    "indicator_type": "ip",
                    "indicator_value": "192.0.2.3",
                    "pulse_id": 3,
                    "pulse_name": "stale-pulse",
                    "pulse_created": now - timedelta(days=45),
                    "is_aggregator": False,
                    "source": "alpha",
                    "source_url": "https://alpha.example/c",
                    "fetched_at": now - timedelta(days=45),
                },
            ],
        )

    with TestSession() as test_session:
        result = _summarize_from_database(test_session, 30)
        assert result["total"] == 6
        assert result["aggregator_refs"] == 3
        assert result["non_aggregator_refs"] == 3
        assert result["distinct_pulses"] == 3
        assert result["stale_count"] == 1
        assert result["stale_days"] == 30
        assert {"source": "beta", "count": 3} in result["by_source"]
        assert {"source": "alpha", "count": 3} in result["by_source"]

    print("PASS")