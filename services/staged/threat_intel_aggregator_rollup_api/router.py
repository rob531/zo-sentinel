from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_threat_intel_aggregator_rollup as _logic_get_threat_intel_aggregator_rollup
except ImportError:
    _logic_get_threat_intel_aggregator_rollup = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class ThreatIntelSourceRollup(BaseModel):
    source: str
    pulse_name: str
    is_aggregator: bool
    indicators: int
    indicator_types: int
    pulses: int
    last_fetched_at: Optional[str]


class ThreatIntelAggregatorRollup(BaseModel):
    total: int
    aggregator_rows: int
    first_party_rows: int
    last_fetched_at: Optional[str]
    sources: list[ThreatIntelSourceRollup]


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _build_rollup(session: Session) -> dict[str, Any]:
    total = session.execute(
        select(func.count()).select_from(ThreatIntelRef)
    ).scalar() or 0

    aggregator_rows = session.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(ThreatIntelRef.is_aggregator.is_(True))
    ).scalar() or 0

    first_party_rows = session.execute(
        select(func.count())
        .select_from(ThreatIntelRef)
        .where(ThreatIntelRef.is_aggregator.is_(False))
    ).scalar() or 0

    newest_fetched_at = session.execute(
        select(func.max(ThreatIntelRef.fetched_at))
    ).scalar()

    grouped = session.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            func.count(ThreatIntelRef.id).label("indicators"),
            func.count(func.distinct(ThreatIntelRef.indicator_type)).label(
                "indicator_types"
            ),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulses"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
        )
    ).all()

    sources = [
        {
            "source": row.source or "",
            "pulse_name": row.pulse_name or "",
            "is_aggregator": bool(row.is_aggregator),
            "indicators": int(row.indicators or 0),
            "indicator_types": int(row.indicator_types or 0),
            "pulses": int(row.pulses or 0),
            "last_fetched_at": _iso8601(row.last_fetched_at),
        }
        for row in grouped
    ]
    sources.sort(
        key=lambda item: (
            -item["indicators"],
            item["source"],
            item["pulse_name"],
        )
    )

    return {
        "total": int(total),
        "aggregator_rows": int(aggregator_rows),
        "first_party_rows": int(first_party_rows),
        "last_fetched_at": _iso8601(newest_fetched_at),
        "sources": sources,
    }


@router.get(
    "/threat-intel/aggregators",
    response_model=ThreatIntelAggregatorRollup,
)
def get_threat_intel_aggregators(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _build_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        test_session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    pulse_id="agg-pulse-1",
                    pulse_name="Vendor Aggregator Feed",
                    pulse_created=datetime(2026, 1, 1, 12, 0, 0),
                    is_aggregator=True,
                    source="example-aggregator",
                    source_url="https://example.invalid/feed",
                    fetched_at=datetime(2026, 1, 2, 12, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="agg-pulse-1",
                    pulse_name="Vendor Aggregator Feed",
                    pulse_created=datetime(2026, 1, 1, 12, 0, 0),
                    is_aggregator=True,
                    source="example-aggregator",
                    source_url="https://example.invalid/feed",
                    fetched_at=datetime(2026, 1, 2, 13, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="cve",
                    indicator_value="CVE-2026-0001",
                    pulse_id="first-party-pulse-1",
                    pulse_name="First Party Feed",
                    pulse_created=datetime(2026, 1, 3, 12, 0, 0),
                    is_aggregator=False,
                    source="example-first-party",
                    source_url="https://example.invalid/first-party",
                    fetched_at=datetime(2026, 1, 4, 12, 0, 0),
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/aggregators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["aggregator_rows"] == 2
    assert payload["first_party_rows"] == 1
    assert len(payload["sources"]) == 2
    print("PASS")