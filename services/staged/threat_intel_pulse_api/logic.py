# services/staged/threat_intel_pulse_api/logic.py
from datetime import datetime
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import desc, func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, McpServerRegistry


class Pulse(BaseModel):
    pulse_id: str
    pulse_name: str
    indicator_count: int
    indicator_types: List[str]
    flagged_server_count: int
    aggregator: bool
    source: str
    latest_fetched_at: datetime


class PulsesResponse(BaseModel):
    pulses: List[Pulse]


def _get_flagged_server_count(session: Session) -> int:
    """Count servers whose risk tier is not LOW."""
    return (
        session.query(func.count())
        .filter(McpServerRegistry.risk_tier != "LOW")
        .scalar()
    )


def _get_indicator_types(session: Session, pulse_id: str) -> List[str]:
    """Return distinct indicator types for a given pulse."""
    rows = (
        session.query(distinct(ThreatIntelRef.indicator_type))
        .filter(ThreatIntelRef.pulse_id == pulse_id)
        .all()
    )
    return [r[0] for r in rows if r[0] is not None]


def get_pulses(
    limit: int = 100,
    aggregator_only: Optional[bool] = None,
    session: Session = Depends(get_session),
) -> PulsesResponse:
    """
    Build a roll‑up of threat‑intel pulses.

    * `limit` – maximum number of pulses to return.
    * `aggregator_only` – if set, filter on the `is_aggregator` flag.
    """
    base_q = (
        session.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            func.count().label("indicator_count"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
        )
    )

    if aggregator_only is not None:
        base_q = base_q.filter(ThreatIntelRef.is_aggregator == aggregator_only)

    rows = (
        base_q.order_by(desc("latest_fetched_at"))
        .limit(limit)
        .all()
    )

    flagged_server_count = _get_flagged_server_count(session)

    pulses: List[Pulse] = []
    for r in rows:
        pulses.append(
            Pulse(
                pulse_id=str(r.pulse_id),
                pulse_name=r.pulse_name,
                indicator_count=r.indicator_count,
                indicator_types=_get_indicator_types(session, r.pulse_id),
                flagged_server_count=flagged_server_count,
                aggregator=r.is_aggregator,
                source=r.source,
                latest_fetched_at=r.latest_fetched_at,
            )
        )

    return PulsesResponse(pulses=pulses)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    sess = TestSession()
    now = datetime.utcnow()

    # Threat intel pulses
    sess.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="typeA",
                indicator_value="valueA",
                pulse_id="p1",
                pulse_name="Pulse One",
                pulse_created=now,
                is_aggregator=True,
                source="src1",
                source_url="http://example.com/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="typeB",
                indicator_value="valueB",
                pulse_id="p1",
                pulse_name="Pulse One",
                pulse_created=now,
                is_aggregator=True,
                source="src1",
                source_url="http://example.com/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="typeC",
                indicator_value="valueC",
                pulse_id="p2",
                pulse_name="Pulse Two",
                pulse_created=now,
                is_aggregator=False,
                source="src2",
                source_url="http://example.com/2",
                fetched_at=now,
            ),
        ]
    )

    # Server registry entries
    sess.add_all(
        [
            McpServerRegistry(
                server_id="s1",
                name="Server One",
                risk_tier="HIGH_RISK_ISOLATED",
                verdict="UNKNOWN",
                trust_score=0.5,
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="s2",
                name="Server Two",
                risk_tier="LOW",
                verdict="UNKNOWN",
                trust_score=0.9,
                last_assessed=now,
            ),
        ]
    )
    sess.commit()

    # ------------------------------------------------------------------- #
    # Dependency override for the test
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Execute the logic
    # ------------------------------------------------------------------- #
    resp = get_pulses(limit=10, aggregator_only=None, session=get_test_session())

    # ------------------------------------------------------------------- #
    # Assertions per acceptance criteria
    # ------------------------------------------------------------------- #
    assert len(resp.pulses) == 2, "expected two pulses"
    agg_pulse = next(p for p in resp.pulses if p.aggregator)
    assert agg_pulse.flagged_server_count == 1, "expected one flagged server"
    print("PASS")