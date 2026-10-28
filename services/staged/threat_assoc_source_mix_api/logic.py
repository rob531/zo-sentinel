"""Logic for the Threat Association Source Mix API.

Provides a single function `get_source_mix` that aggregates data from the
`ThreatIntelRef` table.

The function is used by the router to return a JSON payload with the total
number of threat intel references, a breakdown by source, and a breakdown by
indicator type.
"""

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_source_mix(db: Session) -> Dict[str, Any]:
    """Return aggregated threat intel reference statistics.

    The result has the shape:
    {
        "total": int,
        "by_source": [
            {
                "source": str,
                "count": int,
                "distinct_pulses": int,
                "newest_fetched_at": datetime,
            },
            ...
        ],
        "by_indicator_type": [
            {
                "indicator_type": str,
                "count": int,
            },
            ...
        ],
    }
    """
    # Total count
    total = db.execute(
        db.query(func.count(ThreatIntelRef.id)).statement
    ).scalar_one()

    # By source aggregation
    by_source_rows = (
        db.query(
            ThreatIntelRef.source.label("source"),
            func.count().label("count"),
            func.count(distinct(ThreatIntelRef.pulse_name)).label("distinct_pulses"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
        .all()
    )
    by_source: List[Dict[str, Any]] = [
        {
            "source": row.source,
            "count": row.count,
            "distinct_pulses": row.distinct_pulses,
            "newest_fetched_at": row.newest_fetched_at,
        }
        for row in by_source_rows
    ]

    # By indicator type aggregation
    by_indicator_rows = (
        db.query(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count().label("count"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
        .all()
    )
    by_indicator_type: List[Dict[str, Any]] = [
        {"indicator_type": row.indicator_type, "count": row.count}
        for row in by_indicator_rows
    ]

    return {
        "total": total,
        "by_source": by_source,
        "by_indicator_type": by_indicator_type,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite database and bind the real model metadata.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables for the imported models.
    ThreatIntelRef.__table__.metadata.create_all(engine)

    # Populate test data: 4 rows across 2 sources and 2 indicator types.
    test_data = [
        {
            "id": 1,
            "indicator_type": "ip",
            "indicator_value": "1.2.3.4",
            "source": "SourceA",
            "source_url": "http://example.com/a1",
            "pulse_name": "PulseX",
            "pulse_created": datetime(2023, 1, 1, 12, 0, 0),
            "fetched_at": datetime(2023, 1, 2, 12, 0, 0),
            "is_aggregator": False,
            "pulse_id": "p1",
        },
        {
            "id": 2,
            "indicator_type": "domain",
            "indicator_value": "example.com",
            "source": "SourceA",
            "source_url": "http://example.com/a2",
            "pulse_name": "PulseY",
            "pulse_created": datetime(2023, 1, 3, 12, 0, 0),
            "fetched_at": datetime(2023, 1, 4, 12, 0, 0),
            "is_aggregator": False,
            "pulse_id": "p2",
        },
        {
            "id": 3,
            "indicator_type": "ip",
            "indicator_value": "5.6.7.8",
            "source": "SourceB",
            "source_url": "http://example.com/b1",
            "pulse_name": "PulseX",
            "pulse_created": datetime(2023, 1, 5, 12, 0, 0),
            "fetched_at": datetime(2023, 1, 6, 12, 0, 0),
            "is_aggregator": False,
            "pulse_id": "p1",
        },
        {
            "id": 4,
            "indicator_type": "domain",
            "indicator_value": "test.org",
            "source": "SourceB",
            "source_url": "http://example.com/b2",
            "pulse_name": "PulseZ",
            "pulse_created": datetime(2023, 1, 7, 12, 0, 0),
            "fetched_at": datetime(2023, 1, 8, 12, 0, 0),
            "is_aggregator": False,
            "pulse_id": "p3",
        },
    ]

    # Insert test rows.
    with SessionLocal() as sess:
        for row in test_data:
            sess.add(ThreatIntelRef(**row))
        sess.commit()

        # Run the logic.
        result = get_source_mix(sess)

    # Assertions per the acceptance criteria.
    assert result["total"] == 4, f"expected total 4, got {result['total']}"
    # Find SourceA entry.
    source_a = next((s for s in result["by_source"] if s["source"] == "SourceA"), None)
    assert source_a is not None, "SourceA entry missing"
    assert source_a["count"] == 2, f"SourceA count expected 2, got {source_a['count']}"
    assert source_a["distinct_pulses"] == 2, (
        f"SourceA distinct_pulses expected 2, got {source_a['distinct_pulses']}"
    )
    # Ensure indicator type breakdown sums to total.
    indicator_sum = sum(i["count"] for i in result["by_indicator_type"])
    assert indicator_sum == result["total"], "indicator type counts do not sum to total"

    print("PASS")