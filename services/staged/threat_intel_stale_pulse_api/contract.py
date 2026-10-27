from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Generator, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import and_, case, create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class SourcePulseFreshness(BaseModel):
    source: str
    total: int
    stale: int
    undated: int
    newest_pulse_created: Optional[str]
    age_days: Optional[int]


class StalePulseResponse(BaseModel):
    days: int
    cutoff: str
    sources: list[SourcePulseFreshness]
    totals: dict[str, int]


router = APIRouter(prefix="/api")


def _as_date(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


@router.get(
    "/threat/pulses/stale",
    response_model=StalePulseResponse,
)
def stale_pulses(
    days: int = 14,
    session: Session = Depends(get_session),
) -> StalePulseResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    stale_expression = case(
        (
            and_(
                ThreatIntelRef.pulse_created.is_not(None),
                ThreatIntelRef.pulse_created < cutoff,
            ),
            1,
        ),
        else_=0,
    )

    rows = session.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_created,
            stale_expression.label("stale"),
        )
    ).all()

    grouped: dict[str, dict[str, object]] = {}
    for source_value, pulse_created, stale in rows:
        source = str(source_value) if source_value is not None else ""
        entry = grouped.setdefault(
            source,
            {
                "total": 0,
                "stale": 0,
                "undated": 0,
                "newest": None,
            },
        )
        entry["total"] = int(entry["total"]) + 1
        if pulse_created is None:
            entry["undated"] = int(entry["undated"]) + 1
        else:
            if int(stale or 0):
                entry["stale"] = int(entry["stale"]) + 1
            pulse_date = _as_date(pulse_created)
            if pulse_date is not None and (
                entry["newest"] is None or pulse_date > entry["newest"]
            ):
                entry["newest"] = pulse_date

    sources: list[SourcePulseFreshness] = []
    for source in sorted(grouped):
        entry = grouped[source]
        newest = entry["newest"]
        age_days = None
        if isinstance(newest, date):
            age_days = max(0, (date.today() - newest).days)
        sources.append(
            SourcePulseFreshness(
                source=source,
                total=int(entry["total"]),
                stale=int(entry["stale"]),
                undated=int(entry["undated"]),
                newest_pulse_created=newest.isoformat() if newest else None,
                age_days=age_days,
            )
        )

    return StalePulseResponse(
        days=days,
        cutoff=cutoff.date().isoformat(),
        sources=sources,
        totals={
            "total": sum(item.total for item in sources),
            "stale": sum(item.stale for item in sources),
            "undated": sum(item.undated for item in sources),
        },
    )


app = FastAPI(title="Threat Intel Stale Pulse API")
app.include_router(router)


def run() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    ThreatIntelRef.__table__.create(bind=test_engine)

    now = datetime.utcnow()
    rows = [
        {
            "indicator_type": "ip",
            "indicator_value": "192.0.2.10",
            "pulse_id": "pulse-old",
            "pulse_name": "Old Pulse",
            "pulse_created": now - timedelta(days=30),
            "is_aggregator": False,
            "source": "alpha",
            "source_url": "https://alpha.example/pulse-old",
            "fetched_at": now,
        },
        {
            "indicator_type": "domain",
            "indicator_value": "old.example",
            "pulse_id": "pulse-old-2",
            "pulse_name": "Old Pulse 2",
            "pulse_created": now - timedelta(days=20),
            "is_aggregator": False,
            "source": "alpha",
            "source_url": "https://alpha.example/pulse-old-2",
            "fetched_at": now,
        },
        {
            "indicator_type": "ip",
            "indicator_value": "192.0.2.11",
            "pulse_id": "pulse-undated",
            "pulse_name": "Undated Pulse",
            "pulse_created": None,
            "is_aggregator": False,
            "source": "alpha",
            "source_url": "https://alpha.example/pulse-undated",
            "fetched_at": now,
        },
        {
            "indicator_type": "domain",
            "indicator_value": "recent.example",
            "pulse_id": "pulse-recent",
            "pulse_name": "Recent Pulse",
            "pulse_created": now - timedelta(days=2),
            "is_aggregator": False,
            "source": "beta",
            "source_url": "https://beta.example/pulse-recent",
            "fetched_at": now,
        },
        {
            "indicator_type": "url",
            "indicator_value": "https://beta.example/indicator",
            "pulse_id": "pulse-beta-2",
            "pulse_name": "Beta Pulse 2",
            "pulse_created": now - timedelta(days=5),
            "is_aggregator": True,
            "source": "beta",
            "source_url": "https://beta.example/pulse-beta-2",
            "fetched_at": now,
        },
    ]

    with TestSession() as session:
        session.execute(insert(ThreatIntelRef.__table__), rows)
        session.commit()

    def override_session() -> Generator[Session, None, None]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/threat/pulses/stale?days=14")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["totals"]["total"] == 5, payload
            assert payload["totals"]["undated"] == 1, payload
            assert any(source["stale"] > 0 for source in payload["sources"]), payload
    finally:
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()