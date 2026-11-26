"""logic.py -- data/computation layer for threat_intel_ref_age_api.

Provides a function that computes freshness statistics for ThreatIntelRef
records and a minimal FastAPI app for the self‑test.
"""

from __future__ import annotations

import datetime
from typing import Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import ThreatIntelRef

# --------------------------------------------------------------------------- #
# Core computation
# --------------------------------------------------------------------------- #
def compute_ref_age_stats(db: Session, days: int) -> Dict:
    """Return age/staleness statistics for ThreatIntelRef rows.

    A ref is *stale* when its ``fetched_at`` timestamp is older than the
    ``days`` window.
    """
    threshold = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    rows = db.execute(select(ThreatIntelRef)).scalars().all()

    # Group rows by source
    by_source: Dict[str, List[ThreatIntelRef]] = {}
    for row in rows:
        by_source.setdefault(row.source, []).append(row)

    by_source_out = []
    total_refs = 0
    total_stale = 0

    for source, refs in by_source.items():
        refs_count = len(refs)
        aggregators = sum(1 for r in refs if r.is_aggregator)
        stale_refs = sum(1 for r in refs if r.fetched_at < threshold)
        stale_pct = (stale_refs / refs_count) * 100 if refs_count else 0.0
        fetched_times = [r.fetched_at for r in refs]
        oldest = min(fetched_times).isoformat() if fetched_times else None
        newest = max(fetched_times).isoformat() if fetched_times else None

        by_source_out.append(
            {
                "source": source,
                "refs": refs_count,
                "aggregators": aggregators,
                "stale_refs": stale_refs,
                "stale_pct": stale_pct,
                "oldest_fetched_at": oldest,
                "newest_fetched_at": newest,
            }
        )

        total_refs += refs_count
        total_stale += stale_refs

    totals = {
        "refs": total_refs,
        "sources": len(by_source),
        "stale_refs": total_stale,
        "stale_pct": (total_stale / total_refs) * 100 if total_refs else 0.0,
    }

    return {"days": days, "by_source": by_source_out, "totals": totals}


# --------------------------------------------------------------------------- #
# Minimal FastAPI app for the self‑test
# --------------------------------------------------------------------------- #
app = FastAPI()


@app.get("/api/threat-intel/ref-age")
def ref_age_endpoint(days: int = 30, db: Session = Depends(get_session)):
    """FastAPI endpoint delegating to the core computation."""
    return compute_ref_age_stats(db, days)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # In‑memory SQLite for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as s:
            yield s

    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = datetime.datetime.utcnow()
    fresh_time = now - datetime.timedelta(days=10)
    stale_time = now - datetime.timedelta(days=40)

    with TestSession() as sess:
        sess.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="1.2.3.4",
                    pulse_id="p1",
                    pulse_name="Pulse One",
                    pulse_created=now,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example",
                    fetched_at=fresh_time,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.com",
                    pulse_id="p2",
                    pulse_name="Pulse Two",
                    pulse_created=now,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example",
                    fetched_at=fresh_time,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="hash",
                    indicator_value="deadbeef",
                    pulse_id="p3",
                    pulse_name="Pulse Three",
                    pulse_created=now,
                    is_aggregator=False,
                    source="abuse.ch",
                    source_url="https://abuse.ch",
                    fetched_at=stale_time,
                ),
            ]
        )
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/threat-intel/ref-age?days=30")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["totals"]["refs"] == 3, "Total refs mismatch"
    assert len(data["by_source"]) == 2, "Source count mismatch"

    abuse = next(s for s in data["by_source"] if s["source"] == "abuse.ch")
    assert abuse["stale_refs"] == 1, "Stale count for abuse.ch incorrect"
    assert abuse["stale_pct"] > 0, "Stale pct for abuse.ch should be > 0"

    print("PASS")