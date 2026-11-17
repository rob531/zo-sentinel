"""logic.py -- data/computation for registry_trust_score_bands service.

Provides a pure function that can be used by a FastAPI router to expose a
GET /api/registry/trust-score-bands endpoint. The function operates over the
real `McpServerRegistry` model via a SQLAlchemy Session.
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def compute_trust_score_bands(
    db: Session, min_score: float = 0.0, max_score: float = 100.0
) -> Dict:
    """Bucket registry rows by trust_score.

    Returns a dict with the shape:
    {
        "bands": [
            {"band": "0-20", "label": "untrusted", "count": int, "pct": float},
            ...
        ],
        "total": int,
        "scored": int,
        "unscored": int,
    }

    * `total` – all rows in the table.
    * `scored` – rows where trust_score is not NULL.
    * `unscored` – rows where trust_score IS NULL.
    * `pct` – (count / scored) * 100 rounded to 2 decimal places.
    """
    # Pull only the trust_score column – everything else is unnecessary.
    rows = db.execute(select(McpServerRegistry.trust_score)).all()

    total = len(rows)
    unscored = sum(1 for (ts,) in rows if ts is None)
    scored = total - unscored

    # Fixed band definitions: (lower, upper, band label, human label)
    band_defs: List[tuple[float, float, str, str]] = [
        (0, 20, "0-20", "untrusted"),
        (20, 40, "20-40", "low"),
        (40, 60, "40-60", "moderate"),
        (60, 80, "60-80", "high"),
        (80, 100, "80-100", "trusted"),
    ]

    bands: List[Dict] = []
    for low, high, band_label, human_label in band_defs:
        if high == 100:
            count = sum(
                1
                for (ts,) in rows
                if ts is not None
                and min_score <= ts <= max_score
                and low <= ts <= high
            )
        else:
            count = sum(
                1
                for (ts,) in rows
                if ts is not None
                and min_score <= ts <= max_score
                and low <= ts < high
            )
        pct = round((count / scored * 100) if scored else 0, 2)
        bands.append(
            {"band": band_label, "label": human_label, "count": count, "pct": pct}
        )

    return {"bands": bands, "total": total, "scored": scored, "unscored": unscored}


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.db import Base

    # In‑memory SQLite for the test.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Minimal required fields for McpServerRegistry.
    rows_to_add = [
        McpServerRegistry(
            server_id="srv-1",
            trust_score=10.0,
            name="srv1",
            registry_source="test",
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="srv-2",
            trust_score=55.0,
            name="srv2",
            registry_source="test",
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="srv-3",
            trust_score=None,
            name="srv3",
            registry_source="test",
            risk_tier="high",
        ),
    ]

    with Session(engine) as sess:
        sess.add_all(rows_to_add)
        sess.commit()

        result = compute_trust_score_bands(sess)

        # Assertions per contract.
        assert result["total"] == 3
        assert result["scored"] == 2
        assert result["unscored"] == 1
        band_counts = sum(b["count"] for b in result["bands"])
        assert band_counts == result["scored"]
        print("PASS")