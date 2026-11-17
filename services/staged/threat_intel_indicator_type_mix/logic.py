"""logic.py -- data/computation for threat_intel_indicator_type_mix service.

Provides a single pure function that operates over a SQLAlchemy Session.
The function aggregates rows from the ThreatIntelRef table to produce a
summary per indicator_type together with overall statistics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select, case
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def indicator_type_mix(db: Session) -> Dict[str, Any]:
    """Return aggregated statistics for threat intel indicator types.

    The result shape matches the contract:

    {
        "generated_at": "<iso8601 utc>",
        "types": [
            {
                "indicator_type": str,
                "rows": int,
                "distinct_values": int,
                "aggregator_rows": int,
                "oldest_fetched_at": "<iso8601 utc>" | None,
                "newest_fetched_at": "<iso8601 utc>" | None,
            },
            ...
        ],
        "total_rows": int,
        "distinct_pulses": int,
    }
    """
    # overall counts
    total_rows = db.execute(
        select(func.count()).select_from(ThreatIntelRef)
    ).scalar_one()

    distinct_pulses = db.execute(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id)))
    ).scalar_one()

    # per‑type aggregation
    agg_stmt = (
        select(
            ThreatIntelRef.indicator_type,
            func.count().label("rows"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
                "distinct_values"
            ),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_rows"),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    )

    rows = db.execute(agg_stmt).all()

    types: List[Dict[str, Any]] = []
    for (
        indicator_type,
        rows_cnt,
        distinct_vals,
        agg_rows,
        oldest,
        newest,
    ) in rows:
        types.append(
            {
                "indicator_type": indicator_type,
                "rows": int(rows_cnt),
                "distinct_values": int(distinct_vals),
                "aggregator_rows": int(agg_rows or 0),
                "oldest_fetched_at": (
                    oldest.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                    if oldest
                    else None
                ),
                "newest_fetched_at": (
                    newest.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                    if newest
                    else None
                ),
            }
        )

    generated_at = (
        datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    )

    return {
        "generated_at": generated_at,
        "types": types,
        "total_rows": int(total_rows),
        "distinct_pulses": int(distinct_pulses),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create tables for the models we need
    ThreatIntelRef.metadata.create_all(engine)

    # Seed data: 5 rows across 2 indicator types, 1 aggregator row
    seed_data = [
        {
            "id": 1,
            "indicator_type": "typeA",
            "indicator_value": "val1",
            "is_aggregator": False,
            "pulse_id": 1,
            "pulse_name": "pulse1",
            "pulse_created": datetime(2022, 12, 31, tzinfo=timezone.utc),
            "source": "src",
            "source_url": "http://example.com/1",
            "fetched_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        },
        {
            "id": 2,
            "indicator_type": "typeA",
            "indicator_value": "val2",
            "is_aggregator": False,
            "pulse_id": 1,
            "pulse_name": "pulse1",
            "pulse_created": datetime(2022, 12, 31, tzinfo=timezone.utc),
            "source": "src",
            "source_url": "http://example.com/2",
            "fetched_at": datetime(2023, 1, 2, tzinfo=timezone.utc),
        },
        {
            "id": 3,
            "indicator_type": "typeA",
            "indicator_value": "val1",  # duplicate value
            "is_aggregator": False,
            "pulse_id": 2,
            "pulse_name": "pulse2",
            "pulse_created": datetime(2022, 12, 31, tzinfo=timezone.utc),
            "source": "src",
            "source_url": "http://example.com/3",
            "fetched_at": datetime(2023, 1, 3, tzinfo=timezone.utc),
        },
        {
            "id": 4,
            "indicator_type": "typeB",
            "indicator_value": "val3",
            "is_aggregator": True,
            "pulse_id": 3,
            "pulse_name": "pulse3",
            "pulse_created": datetime(2022, 12, 31, tzinfo=timezone.utc),
            "source": "src",
            "source_url": "http://example.com/4",
            "fetched_at": datetime(2023, 1, 4, tzinfo=timezone.utc),
        },
        {
            "id": 5,
            "indicator_type": "typeB",
            "indicator_value": "val4",
            "is_aggregator": False,
            "pulse_id": 3,
            "pulse_name": "pulse3",
            "pulse_created": datetime(2022, 12, 31, tzinfo=timezone.utc),
            "source": "src",
            "source_url": "http://example.com/5",
            "fetched_at": datetime(2023, 1, 5, tzinfo=timezone.utc),
        },
    ]

    with SessionLocal() as sess:
        for row in seed_data:
            sess.add(ThreatIntelRef(**row))
        sess.commit()

        result = indicator_type_mix(sess)

    # Assertions per contract
    assert result["total_rows"] == 5, "total_rows mismatch"
    assert result["distinct_pulses"] == 3, "distinct_pulses mismatch"

    # Build a lookup by indicator_type for easier checks
    by_type = {t["indicator_type"]: t for t in result["types"]}

    # typeA expectations
    a = by_type["typeA"]
    assert a["rows"] == 3, "typeA rows"
    assert a["distinct_values"] == 2, "typeA distinct_values"
    assert a["aggregator_rows"] == 0, "typeA aggregator_rows"
    assert a["oldest_fetched_at"] == "2023-01-01T00:00:00Z", "typeA oldest"
    assert a["newest_fetched_at"] == "2023-01-03T00:00:00Z", "typeA newest"

    # typeB expectations
    b = by_type["typeB"]
    assert b["rows"] == 2, "typeB rows"
    assert b["distinct_values"] == 2, "typeB distinct_values"
    assert b["aggregator_rows"] == 1, "typeB aggregator_rows"
    assert b["oldest_fetched_at"] == "2023-01-04T00:00:00Z", "typeB oldest"
    assert b["newest_fetched_at"] == "2023-01-05T00:00:00Z", "typeB newest"

    print("PASS")