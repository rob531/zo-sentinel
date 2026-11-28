"""logic.py -- data/computation layer for the freshness_surface_payload_view service.

Provides a read‑only view over FreshnessStat rows, exposing each surface's
payload together with timing metadata and simple staleness metrics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FreshnessStat


def _median(values: List[float]) -> float:
    """Return the median of a list of numbers (empty list -> 0)."""
    n = len(values)
    if n == 0:
        return 0.0
    sorted_vals = sorted(values)
    mid = n // 2
    if n % 2:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def get_freshness_surfaces(
    db: Session, surface: Optional[str] = None
) -> Dict[str, Any]:
    """
    Retrieve freshness payloads per surface.

    Args:
        db: SQLAlchemy Session.
        surface: Optional exact surface name to filter on.

    Returns:
        Mapping with keys:
            - surfaces: list of dicts ordered by computed_at desc.
                Each dict contains surface, computed_at (ISO string),
                compute_seconds, age_seconds, payload.
            - count: total number of surfaces returned.
            - stale_count: number of surfaces whose age_seconds exceeds the median.
    """
    stmt = select(FreshnessStat)
    if surface:
        stmt = stmt.where(FreshnessStat.surface == surface)
    stmt = stmt.order_by(FreshnessStat.computed_at.desc())
    rows = db.execute(stmt).scalars().all()

    if not rows:
        return {"surfaces": [], "count": 0, "stale_count": 0}

    # newest computed_at for age calculation
    newest: datetime = max(row.computed_at for row in rows)

    surface_dicts: List[Dict[str, Any]] = []
    ages: List[float] = []

    for row in rows:
        age_seconds = (newest - row.computed_at).total_seconds()
        ages.append(age_seconds)
        surface_dicts.append(
            {
                "surface": row.surface,
                "computed_at": row.computed_at.isoformat(),
                "compute_seconds": row.compute_seconds,
                "age_seconds": age_seconds,
                "payload": row.payload,
            }
        )

    median_age = _median(ages)
    stale_count = sum(1 for a in ages if a > median_age)

    return {
        "surfaces": surface_dicts,
        "count": len(surface_dicts),
        "stale_count": stale_count,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for isolated testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create the FreshnessStat table in the temporary DB
    FreshnessStat.__table__.create(bind=engine)

    now = datetime.utcnow()
    test_rows = [
        FreshnessStat(
            surface="surface_a",
            computed_at=now,
            compute_seconds=1.2,
            payload={"value": "a"},
        ),
        FreshnessStat(
            surface="surface_b",
            computed_at=now.replace(second=now.second - 10),
            compute_seconds=2.5,
            payload={"value": "b"},
        ),
        FreshnessStat(
            surface="surface_c",
            computed_at=now.replace(second=now.second - 20),
            compute_seconds=0.8,
            payload={"value": "c"},
        ),
    ]

    db = SessionLocal()
    db.add_all(test_rows)
    db.commit()

    result = get_freshness_surfaces(db)

    assert result["count"] == 3, "expected three surfaces"
    assert result["surfaces"][0]["surface"] == "surface_a", "newest surface first"
    for entry in result["surfaces"]:
        for key in ("surface", "computed_at", "age_seconds", "payload"):
            assert key in entry, f"missing {key} in entry"
    print("PASS")