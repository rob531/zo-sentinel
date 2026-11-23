"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import ThreatIntelRef

# ----------------------------------------------------------------------
# Import the implementation; fall back to an inline version if the module
# does not exist (e.g. during isolated testing).
# ----------------------------------------------------------------------
try:
    from .logic import compute_threat_intel_rollup  # pragma: no cover
except Exception:  # pragma: no cover
    def compute_threat_intel_rollup(
        db: Session, *, days: int = 30, limit: int = 20
    ) -> dict:
        """Return the roll‑up statistics for threat‑intel references.

        The implementation follows the service specification and uses only
        SQLAlchemy ORM constructs.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        # total refs in window
        total_refs_q = (
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(ThreatIntelRef.fetched_at >= cutoff)
        )
        total_refs = db.execute(total_refs_q).scalar_one()

        # by indicator type
        by_indicator_q = (
            select(ThreatIntelRef.indicator_type, func.count())
            .where(ThreatIntelRef.fetched_at >= cutoff)
            .group_by(ThreatIntelRef.indicator_type)
        )
        by_indicator_type = {
            row[0]: row[1] for row in db.execute(by_indicator_q).all()
        }

        # by source
        by_source_q = (
            select(ThreatIntelRef.source, func.count())
            .where(ThreatIntelRef.fetched_at >= cutoff)
            .group_by(ThreatIntelRef.source)
        )
        by_source = {row[0]: row[1] for row in db.execute(by_source_q).all()}

        # distinct pulses
        distinct_pulses_q = (
            select(func.count(func.distinct(ThreatIntelRef.pulse_id)))
            .where(ThreatIntelRef.fetched_at >= cutoff)
        )
        distinct_pulses = db.execute(distinct_pulses_q).scalar_one()

        # aggregator vs direct refs
        aggregator_q = (
            select(func.count())
            .where(
                ThreatIntelRef.fetched_at >= cutoff,
                ThreatIntelRef.is_aggregator.is_(True),
            )
        )
        aggregator_refs = db.execute(aggregator_q).scalar_one()

        direct_q = (
            select(func.count())
            .where(
                ThreatIntelRef.fetched_at >= cutoff,
                ThreatIntelRef.is_aggregator.is_(False),
            )
        )
        direct_refs = db.execute(direct_q).scalar_one()

        # recent pulses (newest N by pulse_created)
        recent_subq = (
            select(
                ThreatIntelRef.pulse_id,
                ThreatIntelRef.pulse_name,
                ThreatIntelRef.pulse_created,
                func.count().label("ref_count"),
            )
            .where(ThreatIntelRef.fetched_at >= cutoff)
            .group_by(
                ThreatIntelRef.pulse_id,
                ThreatIntelRef.pulse_name,
                ThreatIntelRef.pulse_created,
            )
            .order_by(desc(ThreatIntelRef.pulse_created))
            .limit(limit)
        )
        recent_pulses = [
            {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "pulse_created": row.pulse_created,
                "ref_count": row.ref_count,
            }
            for row in db.execute(recent_subq).all()
        ]

        return {
            "window_days": days,
            "total_refs": total_refs,
            "by_indicator_type": by_indicator_type,
            "by_source": by_source,
            "distinct_pulses": distinct_pulses,
            "aggregator_refs": aggregator_refs,
            "direct_refs": direct_refs,
            "recent_pulses": recent_pulses,
        }


router = APIRouter(prefix="/api", tags=["threat_intel_rollup"])


class PulseInfo(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    pulse_created: datetime | None = None
    ref_count: int


class RollupResponse(BaseModel):
    window_days: int = Field(..., description="Number of days in the query window")
    total_refs: int
    by_indicator_type: Dict[str, int]
    by_source: Dict[str, int]
    distinct_pulses: int
    aggregator_refs: int
    direct_refs: int
    recent_pulses: List[PulseInfo]


@router.get(
    "/threat-intel/rollup",
    response_model=RollupResponse,
    summary="Roll up external threat‑intel reference feed",
)
def get_threat_intel_rollup(
    days: int = Query(30, ge=1, description="Trailing days to include"),
    limit: int = Query(20, ge=1, le=100, description="Maximum recent pulses to return"),
    db: Session = Depends(get_session),
) -> RollupResponse:
    """Return aggregated threat‑intel reference statistics."""
    raw = compute_threat_intel_rollup(db, days=days, limit=limit)
    # Convert raw dict into the Pydantic model (nested PulseInfo objects)
    raw["recent_pulses"] = [PulseInfo(**p) for p in raw["recent_pulses"]]
    return RollupResponse(**raw)


# ----------------------------------------------------------------------
# Self‑test (executed when the module is run directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mirrors the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # Dependency override for testing
    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data (4 rows, 2 sources, 2 pulse_ids)
    with SessionLocal() as sess:
        now = datetime.utcnow()
        rows = [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="1.2.3.4",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now - timedelta(days=1),
                is_aggregator=False,
                source="SourceA",
                source_url="https://example.com/a",
                fetched_at=now - timedelta(days=2),
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="example.com",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now - timedelta(days=1),
                is_aggregator=True,
                source="SourceA",
                source_url="https://example.com/a",
                fetched_at=now - timedelta(days=2),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="ip",
                indicator_value="5.6.7.8",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(days=3),
                is_aggregator=False,
                source="SourceB",
                source_url="https://example.com/b",
                fetched_at=now - timedelta(days=4),
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="hash",
                indicator_value="deadbeef",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(days=3),
                is_aggregator=True,
                source="SourceB",
                source_url="https://example.com/b",
                fetched_at=now - timedelta(days=4),
            ),
        ]
        sess.add_all(rows)
        sess.commit()

    client = TestClient(app)

    resp = client.get("/api/threat-intel/rollup?days=30&limit=20")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    assert data["total_refs"] == 4, "total_refs mismatch"
    assert len(data["by_indicator_type"]) >= 1, "by_indicator_type empty"
    assert data["distinct_pulses"] == 2, "distinct_pulses mismatch"
    assert data["aggregator_refs"] + data["direct_refs"] == 4, "ref count mismatch"

    print("PASS")