from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


class IndicatorDetailResponse(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    source_url: str
    is_aggregator: bool
    pulse_id: str
    pulse_name: str
    pulse_created: datetime
    fetched_at: datetime
    refs_count: int


def get_indicator_detail(
    indicator_value: str, session: Session = Depends(get_session)
) -> Dict[str, Any]:
    base_query = session.query(ThreatIntelRef).filter(
        ThreatIntelRef.indicator_value == indicator_value
    )
    latest = (
        base_query.order_by(
            ThreatIntelRef.fetched_at.desc(), ThreatIntelRef.id.desc()
        )
        .first()
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="Indicator not found")

    refs_count = base_query.with_entities(
        func.count(ThreatIntelRef.id)
    ).scalar()

    return {
        "indicator_type": latest.indicator_type,
        "indicator_value": latest.indicator_value,
        "source": latest.source,
        "source_url": latest.source_url,
        "is_aggregator": latest.is_aggregator,
        "pulse_id": latest.pulse_id,
        "pulse_name": latest.pulse_name,
        "pulse_created": latest.pulse_created,
        "fetched_at": latest.fetched_at,
        "refs_count": int(refs_count or 0),
    }


router = APIRouter(prefix="/api")


@router.get(
    "/threat-intel/indicator/{indicator_value}",
    response_model=IndicatorDetailResponse,
)
def indicator_detail(
    indicator_value: str, session: Session = Depends(get_session)
) -> Dict[str, Any]:
    return get_indicator_detail(indicator_value, session)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine)
    ThreatIntelRef.__table__.create(bind=engine)

    session = test_session_factory()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.42",
                    source="seed-source-1",
                    source_url="https://example.test/intel/1",
                    is_aggregator=False,
                    pulse_id="pulse-001",
                    pulse_name="Seed Pulse One",
                    pulse_created=datetime(2024, 1, 1, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 1, 12, 5, 0),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.42",
                    source="seed-source-2",
                    source_url="https://example.test/intel/2",
                    is_aggregator=True,
                    pulse_id="pulse-002",
                    pulse_name="Seed Pulse Two",
                    pulse_created=datetime(2024, 1, 2, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 2, 12, 5, 0),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.42",
                    source="seed-source-3",
                    source_url="https://example.test/intel/3",
                    is_aggregator=False,
                    pulse_id="pulse-003",
                    pulse_name="Seed Pulse Three",
                    pulse_created=datetime(2024, 1, 3, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 3, 12, 5, 0),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Any:
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/threat-intel/indicator/198.51.100.42"
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["indicator_value"] == "198.51.100.42"
            assert payload["pulse_id"] == "pulse-003"
            assert payload["refs_count"] == 3

            missing = client.get(
                "/api/threat-intel/indicator/203.0.113.99"
            )
            assert missing.status_code == 404, missing.text
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()