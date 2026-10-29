# services/staged/threat_intel_refresh_lag_api/contract.py
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import ThreatIntelRef

router = APIRouter()


def _age_band(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds < 3600:
        return "under_1h"
    if seconds < 86400:
        return "1h_to_1d"
    if seconds < 7 * 86400:
        return "1d_to_7d"
    return "over_7d"


class OldestItem(BaseModel):
    id: int
    indicator_type: Optional[str] = None
    source: Optional[str] = None
    fetched_at: datetime
    days_old: float = Field(..., description="Age in days (fractional)")


class RefreshLagResponse(BaseModel):
    generated_at: datetime
    total_refs: int
    stale_over_7d: int
    by_age_band: Dict[str, int]
    by_indicator_type: Dict[str, int]
    by_source: Dict[str, int]
    aggregators: Dict[bool, int]
    oldest: List[OldestItem]


@router.get(
    "/api/threat-intel/refresh-lag",
    response_model=RefreshLagResponse,
    tags=["threat_intel"],
)
def get_refresh_lag(session: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)

    refs = session.query(ThreatIntelRef).all()
    if not refs:
        raise HTTPException(status_code=404, detail="No threat intel references found")

    total_refs = len(refs)

    by_age_band: Dict[str, int] = {"under_1h": 0, "1h_to_1d": 0, "1d_to_7d": 0, "over_7d": 0}
    by_indicator_type: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    aggregators: Dict[bool, int] = {True: 0, False: 0}
    stale_over_7d = 0

    for ref in refs:
        fetched_at = ref.fetched_at.replace(tzinfo=timezone.utc) if ref.fetched_at.tzinfo is None else ref.fetched_at
        delta = now - fetched_at
        band = _age_band(delta)
        by_age_band[band] += 1
        if band == "over_7d":
            stale_over_7d += 1

        # indicator_type aggregation
        itype = ref.indicator_type or "unknown"
        by_indicator_type[itype] = by_indicator_type.get(itype, 0) + 1

        # source aggregation
        src = ref.source or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

        # aggregator flag
        agg = bool(ref.is_aggregator)
        aggregators[agg] = aggregators.get(agg, 0) + 1

    # Oldest 20 rows
    oldest_refs = (
        session.query(ThreatIntelRef)
        .order_by(ThreatIntelRef.fetched_at.asc())
        .limit(20)
        .all()
    )
    oldest: List[OldestItem] = []
    for ref in oldest_refs:
        fetched_at = ref.fetched_at.replace(tzinfo=timezone.utc) if ref.fetched_at.tzinfo is None else ref.fetched_at
        days_old = (now - fetched_at).total_seconds() / 86400.0
        oldest.append(
            OldestItem(
                id=ref.id,
                indicator_type=ref.indicator_type,
                source=ref.source,
                fetched_at=fetched_at,
                days_old=round(days_old, 3),
            )
        )

    return RefreshLagResponse(
        generated_at=now,
        total_refs=total_refs,
        stale_over_7d=stale_over_7d,
        by_age_band=by_age_band,
        by_indicator_type=by_indicator_type,
        by_source=by_source,
        aggregators=aggregators,
        oldest=oldest,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.threat_intel_refresh_lag_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # Build a temporary FastAPI app with overridden DB session
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables for the real model metadata
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    now = datetime.now(timezone.utc)
    seed_data = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="1.2.3.4",
            pulse_id="p1",
            pulse_name="Pulse One",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="shodan",
            source_url="http://example.com/1",
            fetched_at=now - timedelta(minutes=30),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="example.com",
            pulse_id="p2",
            pulse_name="Pulse Two",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="censys",
            source_url="http://example.com/2",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="hash",
            indicator_value="deadbeef",
            pulse_id="p3",
            pulse_name="Pulse Three",
            pulse_created=now - timedelta(days=40),
            is_aggregator=False,
            source="virustotal",
            source_url="http://example.com/3",
            fetched_at=now - timedelta(days=30),
        ),
    ]

    with TestSessionLocal() as sess:
        sess.add_all(seed_data)
        sess.commit()

    client = TestClient(test_app)
    resp = client.get("/api/threat-intel/refresh-lag")
    try:
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert data["total_refs"] == 3, "total_refs mismatch"
        assert data["stale_over_7d"] == 1, "stale_over_7d mismatch"
        assert sum(data["by_age_band"].values()) == 3, "by_age_band sum mismatch"
        print("PASS")
        sys.exit(0)
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)