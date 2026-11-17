"""Logic for threat-intelligence pulse activity."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Set

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_threat_pulse_activity(db: Session, days: int = 7) -> Dict[str, Any]:
    """Return threat-intelligence activity for references fetched recently."""
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.utcnow() - timedelta(days=days)
    references = db.execute(
        select(ThreatIntelRef).where(ThreatIntelRef.fetched_at >= cutoff)
    ).scalars().all()

    counts_by_type: Dict[str, int] = {}
    indicator_values: Set[str] = set()
    pulses: Dict[Any, Dict[str, Any]] = {}

    for reference in references:
        indicator_type = reference.indicator_type or "unknown"
        counts_by_type[indicator_type] = counts_by_type.get(indicator_type, 0) + 1

        if reference.indicator_value is not None:
            indicator_values.add(reference.indicator_value)

        pulse_key = reference.pulse_id
        pulse = pulses.get(pulse_key)
        if pulse is None:
            pulse = {
                "pulse_id": pulse_key,
                "pulse_name": reference.pulse_name or "",
                "ref_count": 0,
                "is_aggregator": bool(reference.is_aggregator),
                "latest_fetched_at": reference.fetched_at,
                "indicator_types": set(),
            }
            pulses[pulse_key] = pulse

        pulse["ref_count"] += 1
        pulse["indicator_types"].add(indicator_type)
        if not pulse["pulse_name"] and reference.pulse_name:
            pulse["pulse_name"] = reference.pulse_name
        if reference.fetched_at is not None:
            latest = pulse["latest_fetched_at"]
            if latest is None or reference.fetched_at > latest:
                pulse["latest_fetched_at"] = reference.fetched_at

    pulse_rows: List[Dict[str, Any]] = []
    for pulse in pulses.values():
        latest_fetched_at = pulse["latest_fetched_at"]
        pulse_rows.append(
            {
                "pulse_id": pulse["pulse_id"],
                "pulse_name": pulse["pulse_name"],
                "ref_count": pulse["ref_count"],
                "is_aggregator": pulse["is_aggregator"],
                "latest_fetched_at": (
                    latest_fetched_at.isoformat()
                    if isinstance(latest_fetched_at, datetime)
                    else latest_fetched_at
                ),
                "indicator_types": sorted(pulse["indicator_types"]),
            }
        )

    pulse_rows.sort(key=lambda row: (str(row["pulse_id"]), row["pulse_name"]))

    return {
        "days": days,
        "total_refs": len(references),
        "unique_indicators": len(indicator_values),
        "counts_by_type": dict(sorted(counts_by_type.items())),
        "pulses": pulse_rows,
    }


# Descriptive aliases retained for callers that use either activity-oriented or
# resource-oriented naming.
list_threat_pulses = get_threat_pulse_activity
get_threat_pulses = get_threat_pulse_activity


router = APIRouter(prefix="/api")


@router.get("/threat/pulses")
def threat_pulses(
    days: int = Query(default=7, ge=1),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Surface recent threat-intelligence pulse activity."""
    return get_threat_pulse_activity(db, days)


api_router = router
app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        now = datetime.utcnow()
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Aggregator Pulse",
                    is_aggregator=True,
                    source="aggregator",
                    source_url="https://aggregator.test/pulse-2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-2",
                    pulse_name="Aggregator Pulse",
                    is_aggregator=True,
                    source="aggregator",
                    source_url="https://aggregator.test/pulse-2",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        response = client.get("/api/threat/pulses?days=7")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_refs"] == 4
        assert len(payload["pulses"]) == 2
        assert any(pulse["is_aggregator"] for pulse in payload["pulses"])

    print("PASS")