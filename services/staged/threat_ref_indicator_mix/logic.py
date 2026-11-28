"""logic.py -- data/computation for the threat_ref_indicator_mix service.

Provides a read‑only rollup of external threat‑intel indicators by type,
source and age. Mirrors the exemplar pattern: pure functions over a SQLAlchemy
Session, no side‑effects, and a __main__ self‑test that seeds an in‑memory
SQLite DB and validates the contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import cast, func, Integer, select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def mix_threat_refs(db: Session, days: int = 30) -> Dict:
    """Return a rollup of threat‑intel references for the past *days*.

    The result shape matches the service contract:

    {
        "days": int,
        "by_indicator_type": [
            {
                "indicator_type": str,
                "count": int,
                "distinct_values": int,
                "last_fetched_at": str,   # ISO‑8601 UTC
            },
            ...
        ],
        "by_source": [
            {
                "source": str,
                "count": int,
                "aggregators": int,
            },
            ...
        ],
        "total": int,
    }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ---- Group by indicator type ------------------------------------------------
    stmt_type = (
        select(
            ThreatIntelRef.indicator_type,
            func.count().label("cnt"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label("distinct_vals"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched"),
        )
        .where(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(func.count().desc())
    )
    rows_type = db.execute(stmt_type).all()

    by_indicator_type: List[Dict] = []
    total = 0
    for indicator_type, cnt, distinct_vals, last_fetched in rows_type:
        total += cnt
        by_indicator_type.append(
            {
                "indicator_type": indicator_type,
                "count": int(cnt),
                "distinct_values": int(distinct_vals),
                "last_fetched_at": (
                    last_fetched.replace(tzinfo=timezone.utc).isoformat()
                    if last_fetched
                    else None
                ),
            }
        )

    # ---- Group by source --------------------------------------------------------
    stmt_source = (
        select(
            ThreatIntelRef.source,
            func.count().label("cnt"),
            func.sum(cast(ThreatIntelRef.is_aggregator, Integer)).label("aggregators"),
        )
        .where(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.source)
        .order_by(func.count().desc())
    )
    rows_source = db.execute(stmt_source).all()

    by_source: List[Dict] = []
    for source, cnt, aggregators in rows_source:
        by_source.append(
            {
                "source": source,
                "count": int(cnt),
                "aggregators": int(aggregators or 0),
            }
        )

    return {
        "days": days,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Self‑test ----------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # In‑memory SQLite for the contract test.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    now = datetime.now(timezone.utc)

    # Seed four rows as described in the acceptance criteria.
    seed = [
        ThreatIntelRef(
            id=1,
            indicator_type="ipv4",
            indicator_value="1.1.1.1",
            pulse_id="p1",
            pulse_name="Pulse 1",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="otx",
            source_url="http://example.com/otx1",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ipv4",
            indicator_value="2.2.2.2",
            pulse_id="p2",
            pulse_name="Pulse 2",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="otx",
            source_url="http://example.com/otx2",
            fetched_at=now - timedelta(days=3),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id="p3",
            pulse_name="Pulse 3",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="abusech",
            source_url="http://example.com/abuse1",
            fetched_at=now - timedelta(days=1),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="sha256",
            indicator_value="abcdef1234567890",
            pulse_id="p4",
            pulse_name="Pulse 4",
            pulse_created=now - timedelta(days=1),
            is_aggregator=True,
            source="abusech",
            source_url="http://example.com/abuse2",
            fetched_at=now - timedelta(days=1),
        ),
    ]

    with SessionLocal() as db:
        db.add_all(seed)
        db.commit()

        result = mix_threat_refs(db, days=30)

        assert result["total"] == 4, "total rows mismatch"
        assert len(result["by_indicator_type"]) == 3, "indicator type groups mismatch"
        ipv4 = next(
            (i for i in result["by_indicator_type"] if i["indicator_type"] == "ipv4"),
            None,
        )
        assert ipv4 is not None and ipv4["count"] == 2, "ipv4 count mismatch"

        print("PASS")