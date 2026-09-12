# services/staged/threat_intel_refs_aggregation/logic.py
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, Pulse, VulnLink  # type: ignore


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
from pydantic import BaseModel


class PulseModel(BaseModel):
    id: int
    name: str
    created: datetime
    source: str


class IndicatorTypeAggModel(BaseModel):
    type: str
    count: int
    pulses: List[PulseModel] = []


class ThreatIntelRefsAggregationResponse(BaseModel):
    indicator_types: List[IndicatorTypeAggModel]
    total_refs: int
    last_fetched: datetime | None = None


# --------------------------------------------------------------------------- #
# Core aggregation logic
# --------------------------------------------------------------------------- #
def _fetch_indicator_aggregates(sess: Session) -> List[IndicatorTypeAggModel]:
    """
    Returns a list of IndicatorTypeAggModel, each containing the indicator type,
    the number of references of that type, and the most recent pulses linked to
    those references (if any).
    """
    # Base aggregation per indicator type
    agg_stmt = (
        select(
            ThreatIntelRef.indicator_type.label("type"),
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    )
    rows = sess.execute(agg_stmt).all()

    result: List[IndicatorTypeAggModel] = []
    for row in rows:
        it_type: str = row.type
        it_count: int = row.cnt

        # Fetch recent pulses for this indicator type.
        # We join ThreatIntelRef -> VulnLink -> Pulse.
        pulse_stmt = (
            select(
                Pulse.id,
                Pulse.name,
                Pulse.created_at.label("created"),
                Pulse.source,
            )
            .join(VulnLink, VulnLink.pulse_id == Pulse.id)
            .join(ThreatIntelRef, ThreatIntelRef.id == VulnLink.threat_intel_ref_id)
            .where(ThreatIntelRef.indicator_type == it_type)
            .order_by(Pulse.created_at.desc())
            .limit(5)
        )
        pulse_rows = sess.execute(pulse_stmt).all()
        pulses = [
            PulseModel(
                id=p.id,
                name=p.name,
                created=p.created,
                source=p.source,
            )
            for p in pulse_rows
        ]

        result.append(
            IndicatorTypeAggModel(
                type=it_type,
                count=it_count,
                pulses=pulses,
            )
        )
    return result


def get_threat_intel_refs_aggregation(
    sess: Session = Depends(get_session),
) -> ThreatIntelRefsAggregationResponse:
    """
    Assemble the aggregation payload for the `/api/threat-intel/refs` endpoint.
    """
    total_refs_stmt = select(func.count(ThreatIntelRef.id))
    total_refs = sess.execute(total_refs_stmt).scalar_one()

    last_fetched_stmt = select(func.max(ThreatIntelRef.fetched_at))
    last_fetched = sess.execute(last_fetched_stmt).scalar_one()

    indicator_aggregates = _fetch_indicator_aggregates(sess)

    return ThreatIntelRefsAggregationResponse(
        indicator_types=indicator_aggregates,
        total_refs=total_refs,
        last_fetched=last_fetched,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # NOTE: The self‑test creates an in‑memory SQLite DB, populates it with a
    # minimal set of rows, and validates the aggregation contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    sess = SessionLocal()

    # Seed minimal data – only the columns we know exist.
    now = datetime.utcnow()
    refs = [
        ThreatIntelRef(id=1, indicator_type="typeA", fetched_at=now),
        ThreatIntelRef(id=2, indicator_type="typeA", fetched_at=now),
        ThreatIntelRef(id=3, indicator_type="typeA", fetched_at=now),
        ThreatIntelRef(id=4, indicator_type="typeB", fetched_at=now),
        ThreatIntelRef(id=5, indicator_type="typeB", fetched_at=now),
    ]
    sess.add_all(refs)
    sess.commit()

    resp = get_threat_intel_refs_aggregation(sess)

    assert resp.total_refs == 5, "total_refs mismatch"
    assert len(resp.indicator_types) == 2, "indicator_types length mismatch"

    type_a = next(it for it in resp.indicator_types if it.type == "typeA")
    assert type_a.count == 3, "typeA count mismatch"

    print("PASS")