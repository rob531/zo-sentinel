"""services/staged/registry_freshness_status/logic.py

Data/computation layer for the *registry_freshness_status* service.

Provides a single pure function that, given a SQLAlchemy Session, returns a
dictionary describing the freshness of each surface stored in the
``freshness_stats`` table.

The function is deliberately side‑effect free – it only reads from the
database and performs in‑memory calculations.  The FastAPI router (see
``router.py``) calls this function and returns its result as the HTTP
response body.
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FreshnessStat


def get_freshness_status(db: Session) -> Dict[str, object]:
    """
    Return a freshness status payload.

    The payload shape matches the contract expected by
    ``services/staged/registry_freshness_status/router.py``::

        {
            "generated_at": "<ISO‑8601 UTC timestamp>",
            "surfaces": [
                {
                    "surface": "<surface name>",
                    "computed_at": "<ISO‑8601 UTC timestamp>",
                    "age_seconds": <int>,
                    "compute_seconds": <int>,
                    "stale": <bool>
                },
                ...
            ],
            "stale_count": <int>
        }

    A surface is considered *stale* when its ``age_seconds`` exceeds
    3600 (one hour).

    Parameters
    ----------
    db:
        An active SQLAlchemy Session (provided by ``app.db.get_session``).

    Returns
    -------
    dict
        The freshness status payload.
    """
    now = _dt.datetime.utcnow()

    rows = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
    ).all()

    surfaces: List[Dict[str, object]] = []
    stale_count = 0

    for surface, computed_at, compute_seconds in rows:
        # ``computed_at`` is stored as a UTC datetime in the DB.
        age_seconds = int((now - computed_at).total_seconds())
        stale = age_seconds > 3600
        if stale:
            stale_count += 1

        surfaces.append(
            {
                "surface": surface,
                "computed_at": computed_at.isoformat() + "Z",
                "age_seconds": age_seconds,
                "compute_seconds": int(compute_seconds),
                "stale": stale,
            }
        )

    return {
        "generated_at": now.isoformat() + "Z",
        "surfaces": surfaces,
        "stale_count": stale_count,
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, creates the required tables,
    # seeds two rows (one fresh, one stale), invokes ``get_freshness_status`` and
    # asserts the expected contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Setup in‑memory DB
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base  # noqa: E402  (import after engine creation)

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    now = _dt.datetime.utcnow()
    fresh = FreshnessStat(
        surface="fresh_surface",
        payload={},  # payload is required but not used in the output
        computed_at=now,
        compute_seconds=5,
    )
    stale = FreshnessStat(
        surface="stale_surface",
        payload={},
        computed_at=now - _dt.timedelta(hours=2),  # 2 hours old → stale
        compute_seconds=5,
    )

    with SessionLocal() as sess:
        sess.add_all([fresh, stale])
        sess.commit()

        # ------------------------------------------------------------------- #
        # Execute logic and validate
        # ------------------------------------------------------------------- #
        result = get_freshness_status(sess)

        assert isinstance(result, dict), "Result must be a dict"
        assert "generated_at" in result, "Missing generated_at"
        assert "surfaces" in result, "Missing surfaces list"
        assert "stale_count" in result, "Missing stale_count"

        surfaces = result["surfaces"]
        assert isinstance(surfaces, list), "surfaces must be a list"
        assert len(surfaces) == 2, f"Expected 2 surfaces, got {len(surfaces)}"

        # Find the fresh and stale entries
        fresh_entry = next(s for s in surfaces if s["surface"] == "fresh_surface")
        stale_entry = next(s for s in surfaces if s["surface"] == "stale_surface")

        assert fresh_entry["stale"] is False, "Fresh surface incorrectly marked stale"
        assert stale_entry["stale"] is True, "Stale surface not marked stale"

        assert result["stale_count"] == 1, f"Expected stale_count 1, got {result['stale_count']}"

    print("PASS")