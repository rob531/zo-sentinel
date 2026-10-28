# services/staged/threatintel_aggregator_split_api/contract.py
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import ThreatIntelRef

router = APIRouter(prefix="/api")


def _clamp_days(days: int) -> int:
    """Clamp days to the allowed range 1‑365."""
    return max(1, min(365, days))


@router.get("/threat-intel/aggregator-split")
def get_aggregated_intel(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Dict:
    """Return aggregated threat‑intel statistics for the last *days* days."""
    days = _clamp_days(days)
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows: List[ThreatIntelRef] = (
        session.query(ThreatIntelRef)
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .all()
    )

    total = len(rows)
    aggregator_rows = [r for r in rows if r.is_aggregator]
    direct_rows = [r for r in rows if not r.is_aggregator]

    aggregator_count = len(aggregator_rows)

    # source aggregations
    agg_source_counter: Counter = Counter(r.source for r in aggregator_rows)
    direct_source_counter: Counter = Counter(r.source for r in direct_rows)

    aggregators = [{"source": src, "count": cnt} for src, cnt in agg_source_counter.items()]
    direct = [{"source": src, "count": cnt} for src, cnt in direct_source_counter.items()]

    # indicator‑type aggregations
    by_indicator: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "aggregator_count": 0, "direct_count": 0}
    )
    for r in rows:
        stats = by_indicator[r.indicator_type]
        stats["total"] += 1
        if r.is_aggregator:
            stats["aggregator_count"] += 1
        else:
            stats["direct_count"] += 1

    by_indicator_type = [
        {
            "indicator_type": itype,
            "total": vals["total"],
            "aggregator_count": vals["aggregator_count"],
            "direct_count": vals["direct_count"],
        }
        for itype, vals in by_indicator.items()
    ]

    ratio = round(aggregator_count / total, 4) if total else 0.0

    return {
        "days": days,
        "aggregators": aggregators,
        "direct": direct,
        "total": total,
        "aggregator_count": aggregator_count,
        "aggregator_ratio": ratio,
        "by_indicator_type": by_indicator_type,
    }


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.threatintel_aggregator_split_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build a temporary FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite setup
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    now = datetime.utcnow()
    seed = [
        ThreatIntelRef(
            id=1,
            indicator_type="type1",
            indicator_value="val1",
            pulse_id="p1",
            pulse_name="Pulse 1",
            pulse_created=now - timedelta(days=5),
            is_aggregator=True,
            source="srcA",
            source_url="http://example.com/a",
            fetched_at=now - timedelta(days=5),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="type2",
            indicator_value="val2",
            pulse_id="p2",
            pulse_name="Pulse 2",
            pulse_created=now - timedelta(days=10),
            is_aggregator=True,
            source="srcA",
            source_url="http://example.com/a",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="type1",
            indicator_value="val3",
            pulse_id="p3",
            pulse_name="Pulse 3",
            pulse_created=now - timedelta(days=2),
            is_aggregator=False,
            source="srcB",
            source_url="http://example.com/b",
            fetched_at=now - timedelta(days=2),
        ),
        # Outside the default 30‑day window
        ThreatIntelRef(
            id=4,
            indicator_type="type1",
            indicator_value="val4",
            pulse_id="p4",
            pulse_name="Pulse 4",
            pulse_created=now - timedelta(days=40),
            is_aggregator=False,
            source="srcC",
            source_url="http://example.com/c",
            fetched_at=now - timedelta(days=40),
        ),
    ]

    with TestSession() as s:
        s.add_all(seed)
        s.commit()

    client = TestClient(app)
    resp = client.get("/api/threat-intel/aggregator-split?days=30")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["total"] == 3
        assert data["aggregator_count"] == 2
        assert data["aggregator_ratio"] == 0.6667
        # by_indicator_type checks
        by_type = {i["indicator_type"]: i for i in data["by_indicator_type"]}
        assert by_type["type1"]["total"] == 2
        assert by_type["type1"]["aggregator_count"] == 1
        assert by_type["type1"]["direct_count"] == 1
        assert by_type["type2"]["total"] == 1
        assert by_type["type2"]["aggregator_count"] == 1
        assert by_type["type2"]["direct_count"] == 0
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)