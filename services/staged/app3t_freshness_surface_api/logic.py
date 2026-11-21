"""logic.py -- data/computation layer for app3t_freshness_surface_api.

Provides a pure function that queries the real FreshnessStat table and
returns a JSON‑serialisable structure for the API router.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import FreshnessStat


def _as_dict(row: FreshnessStat, stale: bool) -> Dict[str, Any]:
    """Convert a FreshnessStat ORM instance to the API dict shape."""
    return {
        "surface": row.surface,
        "computed_at": row.computed_at.isoformat(),
        "compute_seconds": float(row.compute_seconds) if row.compute_seconds is not None else None,
        "payload": row.payload,
        "stale": stale,
    }


def get_freshness_surfaces(
    db: Session,
    *,
    stale_only: bool = False,
    max_age_minutes: int = 60,
) -> Dict[str, Any]:
    """
    Return freshness statistics for all surfaces.

    Parameters
    ----------
    db: Session
        SQLAlchemy session bound to the application database.
    stale_only: bool, default False
        If True, only rows whose ``computed_at`` is older than ``max_age_minutes``
        are returned.
    max_age_minutes: int, default 60
        Age threshold (in minutes) used to decide whether a row is stale.

    Returns
    -------
    dict
        ``{"count": int, "surfaces": [ ... ]}`` where each surface dict follows
        the contract described in the service specification.
    """
    now = datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(minutes=max_age_minutes)

    stmt = select(FreshnessStat).order_by(FreshnessStat.surface.asc())
    rows: List[FreshnessStat] = db.execute(stmt).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        is_stale = row.computed_at < cutoff
        if stale_only and not is_stale:
            continue
        surfaces.append(_as_dict(row, is_stale))

    return {"count": len(surfaces), "surfaces": surfaces}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite engine that mimics the real app DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Override the app's get_session dependency for the test.
    def test_get_session() -> Session:
        return SessionLocal()

    # Seed three rows.
    now = datetime.datetime.utcnow()
    recent = FreshnessStat(
        surface="surface_recent",
        payload={"rows": 42},
        computed_at=now,
        compute_seconds=1.23,
    )
    old = FreshnessStat(
        surface="surface_old",
        payload={"rows": 7},
        computed_at=now - datetime.timedelta(hours=3),
        compute_seconds=4.56,
    )
    another = FreshnessStat(
        surface="surface_other",
        payload={"status": "ok"},
        computed_at=now - datetime.timedelta(minutes=30),
        compute_seconds=None,
    )

    with SessionLocal() as sess:
        sess.add_all([recent, old, another])
        sess.commit()

        # Default query (no stale filter)
        result = get_freshness_surfaces(sess)
        assert result["count"] == 3, "expected three surfaces"
        # payload {"rows": 42} must appear exactly once
        payloads = [s["payload"] for s in result["surfaces"]]
        assert {"rows": 42} in payloads, "payload {'rows': 42} missing"
        # at least one entry must be stale (old surface)
        stale_flags = [s["stale"] for s in result["surfaces"]]
        assert any(stale_flags), "no stale entries found"

        # Stale‑only query
        stale_result = get_freshness_surfaces(sess, stale_only=True)
        assert stale_result["count"] < 3, "stale_only did not reduce count"

    print("PASS")