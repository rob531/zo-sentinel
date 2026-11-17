"""services/staged/freshness_surface_status/logic.py

Data/computation layer for the freshness_surface_status service.

Provides a pure function that queries the real FreshnessStat table via a
SQLAlchemy Session and returns a structured dict suitable for the FastAPI
router.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

# ---------------------------------------------------------------------------


def _status_bucket(age_seconds: float) -> str:
    """Return freshness bucket based on age in seconds."""
    if age_seconds < 15 * 60:
        return "fresh"
    if age_seconds < 2 * 60 * 60:
        return "stale"
    return "expired"


def get_freshness_surface_status(db: Session) -> Dict[str, Any]:
    """
    Return the current freshness state of every recorded surface.

    The result shape matches the contract expected by the router:

    {
        "generated_at": "<ISO‑8601 timestamp>",
        "surfaces": [
            {
                "surface": "<name>",
                "computed_at": "<ISO‑8601 timestamp>",
                "compute_seconds": <int>,
                "age_seconds": <float>,
                "status": "fresh" | "stale" | "expired",
                "payload": { ... parsed JSON ... }
            },
            ...
        ]
    }

    Surfaces are sorted first by ``status`` (fresh → stale → expired) and then
    alphabetically by ``surface`` name.
    """
    now = datetime.utcnow()

    rows: List[FreshnessStat] = db.execute(select(FreshnessStat)).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        age_seconds = (now - row.computed_at).total_seconds()
        status = _status_bucket(age_seconds)

        # ``payload`` is stored as JSON text; ensure we return a dict.
        if isinstance(row.payload, (str, bytes)):
            try:
                payload_obj = json.loads(row.payload)
            except Exception:
                payload_obj = {}
        else:
            payload_obj = row.payload or {}

        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": row.computed_at.isoformat() + "Z",
                "compute_seconds": int(row.compute_seconds),
                "age_seconds": age_seconds,
                "status": status,
                "payload": payload_obj,
            }
        )

    # Sorting: status order then surface name
    status_order = {"fresh": 0, "stale": 1, "expired": 2}
    surfaces.sort(
        key=lambda s: (status_order.get(s["status"], 99), s["surface"])
    )

    return {"generated_at": now.isoformat() + "Z", "surfaces": surfaces}


# ---------------------------------------------------------------------------
# Self‑test (executed when the module is run directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, creates the FreshnessStat
    # table, seeds three rows, invokes the function and checks the contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create a temporary in‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables for the models we need
    FreshnessStat.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Seed data
    now = datetime.utcnow()
    with SessionLocal() as sess:
        sess.add_all(
            [
                FreshnessStat(
                    surface="surface_fresh",
                    payload=json.dumps({"value": 1}),
                    computed_at=now,
                    compute_seconds=10,
                ),
                FreshnessStat(
                    surface="surface_stale",
                    payload=json.dumps({"value": 2}),
                    computed_at=now - timedelta(minutes=30),
                    compute_seconds=20,
                ),
                FreshnessStat(
                    surface="surface_expired",
                    payload=json.dumps({"value": 3}),
                    computed_at=now - timedelta(hours=5),
                    compute_seconds=30,
                ),
            ]
        )
        sess.commit()

        # Run the logic
        result = get_freshness_surface_status(sess)

        # Basic contract assertions
        assert isinstance(result, dict), "Result must be a dict"
        assert "generated_at" in result, "Missing generated_at"
        assert "surfaces" in result, "Missing surfaces list"
        surfaces = result["surfaces"]
        assert len(surfaces) == 3, f"Expected 3 surfaces, got {len(surfaces)}"

        # Find each surface by name
        by_name = {s["surface"]: s for s in surfaces}
        assert by_name["surface_fresh"]["status"] == "fresh", "Fresh row status mismatch"
        assert (
            by_name["surface_expired"]["status"] == "expired"
        ), "Expired row status mismatch"

        print("PASS")