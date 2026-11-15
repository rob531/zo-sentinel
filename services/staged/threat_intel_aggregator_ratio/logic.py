"""logic.py -- data/computation layer for threat_intel_aggregator_ratio service.

Provides a pure function that operates over a SQLAlchemy Session to compute
aggregator vs specific threat‑intel reference statistics.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import func, select, case
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def aggregator_ratio_stats(db: Session) -> Dict:
    """Return aggregation statistics for threat‑intel references.

    The result shape matches the contract expected by the router:

    {
        "total_refs": int,
        "aggregator_refs": int,
        "specific_refs": int,
        "aggregator_ratio": float,          # 0.0 .. 1.0
        "distinct_pulses": int,
        "by_indicator_type": [
            {
                "indicator_type": str,
                "total": int,
                "aggregator_ratio": float,
            },
            ...
        ],
    }
    """
    # overall counts
    total_refs = db.execute(
        select(func.count()).select_from(ThreatIntelRef)
    ).scalar_one()

    aggregator_refs = db.execute(
        select(func.count()).where(ThreatIntelRef.is_aggregator.is_(True))
    ).scalar_one()

    specific_refs = total_refs - aggregator_refs
    aggregator_ratio = (
        aggregator_refs / total_refs if total_refs else 0.0
    )

    distinct_pulses = db.execute(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id)))
    ).scalar_one()

    # breakdown by indicator_type
    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ),
        )
        .group_by(ThreatIntelRef.indicator_type)
    ).all()

    by_indicator_type: List[Dict] = []
    for indicator_type, total, agg_sum in rows:
        agg_total = int(agg_sum or 0)
        total_int = int(total)
        ratio = agg_total / total_int if total_int else 0.0
        by_indicator_type.append(
            {
                "indicator_type": indicator_type,
                "total": total_int,
                "aggregator_ratio": ratio,
            }
        )

    return {
        "total_refs": int(total_refs),
        "aggregator_refs": int(aggregator_refs),
        "specific_refs": int(specific_refs),
        "aggregator_ratio": aggregator_ratio,
        "distinct_pulses": int(distinct_pulses),
        "by_indicator_type": by_indicator_type,
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine; use the same metadata as the real model.
    engine = create_engine("sqlite:///:memory:", future=True)
    ThreatIntelRef.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        # Seed four rows: two indicator types, half aggregators.
        rows = [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="1.2.3.4",
                pulse_id="pulse-1",
                source="source-a",
                source_url="https://example.com/a",
                is_aggregator=True,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="5.6.7.8",
                pulse_id="pulse-2",
                source="source-b",
                source_url="https://example.com/b",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.com",
                pulse_id="pulse-3",
                source="source-c",
                source_url="https://example.com/c",
                is_aggregator=True,
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="domain",
                indicator_value="test.org",
                pulse_id="pulse-4",
                source="source-d",
                source_url="https://example.com/d",
                is_aggregator=False,
            ),
        ]
        db.add_all(rows)
        db.commit()

        result = aggregator_ratio_stats(db)

        assert result["total_refs"] == 4
        assert abs(result["aggregator_ratio"] - 0.5) < 1e-9
        assert len(result["by_indicator_type"]) == 2
        print("PASS")