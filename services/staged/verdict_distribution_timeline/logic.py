"""
services/staged/verdict_distribution_timeline/logic.py

Logic for the ``verdict_distribution_timeline`` staged service.

Provides a single function ``get_verdict_distribution_timeline`` that
aggregates server records by the date part of ``last_assessed`` and the
``risk_tier`` column, returning a structure suitable for the API contract:

    {
        "series": [
            {"date": "YYYY-MM-DD", "tier": "<risk_tier>", "count": <int>},
            ...
        ]
    }

The implementation uses the real application data layer – the SQLAlchemy
session obtained from ``app.db.get_session`` and the ``McpServerRegistry``
model from ``app.models`` – and performs a single grouped query that is
portable across PostgreSQL back‑ends.

A minimal ``__main__`` self‑test creates an in‑memory SQLite database,
populates it with seed data, invokes the function and validates the
output, printing ``PASS`` on success.
"""

from datetime import datetime, date
from typing import List, Dict

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Base  # Base is required for the self‑test


def get_verdict_distribution_timeline(
    session: Session = Depends(get_session),
) -> Dict[str, List[Dict[str, object]]]:
    """
    Return a time‑series aggregation of server counts per ``risk_tier``.
    The aggregation groups by the date component of ``last_assessed``.
    """
    # Build the grouped query – PostgreSQL compatible
    stmt = (
        select(
            func.date(McpServerRegistry.last_assessed).label("as_of_date"),
            McpServerRegistry.risk_tier,
            func.count().label("cnt"),
        )
        .where(McpServerRegistry.last_assessed.is_not(None))
        .group_by("as_of_date", McpServerRegistry.risk_tier)
        .order_by("as_of_date")
    )

    rows = session.execute(stmt).all()

    series = [
        {
            "date": row.as_of_date.isoformat() if isinstance(row.as_of_date, (date, datetime)) else str(row.as_of_date),
            "tier": row.risk_tier,
            "count": row.cnt,
        }
        for row in rows
    ]

    return {"series": series}


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine – isolated from the production DB
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables according to the real models
    Base.metadata.create_all(engine)

    # Seed data: three servers, two distinct ``last_assessed`` dates,
    # two distinct ``risk_tier`` values.
    seed = [
        McpServerRegistry(
            server_id="srv-1",
            name="server‑one",
            verdict="allow",
            risk_tier="high",
            last_assessed=datetime(2023, 1, 1, 12, 0, 0),
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="server‑two",
            verdict="allow",
            risk_tier="low",
            last_assessed=datetime(2023, 1, 1, 15, 30, 0),
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="server‑three",
            verdict="allow",
            risk_tier="high",
            last_assessed=datetime(2023, 1, 2, 9, 45, 0),
        ),
    ]

    with SessionLocal() as sess:
        sess.add_all(seed)
        sess.commit()

        result = get_verdict_distribution_timeline(sess)

    # Expected aggregation:
    # 2023‑01‑01: high=1, low=1
    # 2023‑01‑02: high=1
    expected = {
        ("2023-01-01", "high"): 1,
        ("2023-01-01", "low"): 1,
        ("2023-01-02", "high"): 1,
    }

    # Verify structure
    assert isinstance(result, dict), "Result must be a dict"
    assert "series" in result, "Result must contain 'series' key"
    series = result["series"]
    assert isinstance(series, list), "'series' must be a list"
    assert len(series) >= 2, "Series length must be at least 2"

    # Build lookup from the returned series
    lookup = {(item["date"], item["tier"]): item["count"] for item in series}
    for key, cnt in expected.items():
        assert lookup.get(key) == cnt, f"Count mismatch for {key}: expected {cnt}, got {lookup.get(key)}"

    print("PASS")