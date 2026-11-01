from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class PulseActivityResponse(BaseModel):
    days: int
    total_refs: int
    unique_indicators: int
    counts_by_type: Dict[str, int]
    pulses: List["PulseActivity"]


class PulseActivity(BaseModel):
    pulse_id: str
    pulse_name: str
    ref_count: int
    is_aggregator: bool
    latest_fetched_at: datetime
    indicator_types: List[str] = Field(default_factory=list)


def get_threat_pulse_activity(
    days: int = Query(default=7, ge=1),
    session: Session = Depends(get_session),
) -> PulseActivityResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    refs = session.execute(
        select(ThreatIntelRef).where(ThreatIntelRef.fetched_at >= cutoff)
    ).scalars().all()

    counts_by_type = Counter(ref.indicator_type for ref in refs)
    unique_indicators = len({ref.indicator_value for ref in refs})

    grouped = defaultdict(list)
    for ref in refs:
        grouped[(str(ref.pulse_id), ref.pulse_name, bool(ref.is_aggregator))].append(ref)

    pulses = []
    for (pulse_id, pulse_name, is_aggregator), pulse_refs in grouped.items():
        latest = max(ref.fetched_at for ref in pulse_refs)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        pulses.append(
            PulseActivity(
                pulse_id=pulse_id,
                pulse_name=pulse_name,
                ref_count=len(pulse_refs),
                is_aggregator=is_aggregator,
                latest_fetched_at=latest,
                indicator_types=sorted({ref.indicator_type for ref in pulse_refs}),
            )
        )

    pulses.sort(key=lambda pulse: (pulse.pulse_id, pulse.pulse_name))

    return PulseActivityResponse(
        days=days,
        total_refs=len(refs),
        unique_indicators=unique_indicators,
        counts_by_type=dict(sorted(counts_by_type.items())),
        pulses=pulses,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/threat/pulses",
    response_model=PulseActivityResponse,
    summary="Threat-intelligence pulse activity",
)
def threat_pulses(
    days: int = Query(default=7, ge=1),
    session: Session = Depends(get_session),
) -> PulseActivityResponse:
    return get_threat_pulse_activity(days=days, session=session)


app = FastAPI(title="Threat Pulse Activity Contract")
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    ThreatIntelRef.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestSession() as session:
            session.add_all(
                [
                    ThreatIntelRef(
                        id=1,
                        indicator_type="ip",
                        indicator_value="192.0.2.1",
                        pulse_id="pulse-alpha",
                        pulse_name="Alpha Pulse",
                        pulse_created=now - timedelta(days=2),
                        is_aggregator=True,
                        source="fixture-source",
                        source_url="https://example.invalid/alpha",
                        fetched_at=now - timedelta(hours=2),
                    ),
                    ThreatIntelRef(
                        id=2,
                        indicator_type="domain",
                        indicator_value="alpha.example.invalid",
                        pulse_id="pulse-alpha",
                        pulse_name="Alpha Pulse",
                        pulse_created=now - timedelta(days=2),
                        is_aggregator=True,
                        source="fixture-source",
                        source_url="https://example.invalid/alpha",
                        fetched_at=now - timedelta(hours=1),
                    ),
                    ThreatIntelRef(
                        id=3,
                        indicator_type="ip",
                        indicator_value="192.0.2.2",
                        pulse_id="pulse-beta",
                        pulse_name="Beta Pulse",
                        pulse_created=now - timedelta(days=1),
                        is_aggregator=False,
                        source="fixture-source",
                        source_url="https://example.invalid/beta",
                        fetched_at=now - timedelta(hours=3),
                    ),
                    ThreatIntelRef(
                        id=4,
                        indicator_type="url",
                        indicator_value="https://beta.example.invalid/path",
                        pulse_id="pulse-beta",
                        pulse_name="Beta Pulse",
                        pulse_created=now - timedelta(days=1),
                        is_aggregator=False,
                        source="fixture-source",
                        source_url="https://example.invalid/beta",
                        fetched_at=now - timedelta(hours=4),
                    ),
                ]
            )
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/threat/pulses", params={"days": 7})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["days"] == 7, body
        assert body["total_refs"] == 4, body
        assert body["unique_indicators"] == 4, body
        assert body["counts_by_type"] == {"domain": 1, "ip": 2, "url": 1}, body
        assert len(body["pulses"]) == 2, body
        assert any(pulse["is_aggregator"] is True for pulse in body["pulses"]), body
        assert all("indicator_value" not in pulse for pulse in body["pulses"]), body
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()