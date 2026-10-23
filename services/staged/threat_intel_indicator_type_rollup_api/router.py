from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_indicator_type_rollup
except ImportError:
    from services.staged.threat_intel_indicator_type_rollup_api.logic import (
        get_indicator_type_rollup,
    )


router = APIRouter()


class IndicatorTypeRollupItem(BaseModel):
    indicator_type: str
    count: int
    distinct_values: int
    aggregator_count: int
    latest_pulse_created: datetime
    latest_source_url: Optional[str] = None


class IndicatorTypeRollupResponse(BaseModel):
    days: int
    total: int
    types: List[IndicatorTypeRollupItem]


@router.get(
    "/api/threat-intel/indicator-types",
    response_model=IndicatorTypeRollupResponse,
)
def read_indicator_type_rollup(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
):
    return get_indicator_type_rollup(session, days)


if __name__ == "__main__":
    from datetime import timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_db = TestSession()
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id=1,
                pulse_name="Recent Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/recent",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.11",
                pulse_id=1,
                pulse_name="Recent Pulse",
                pulse_created=now - timedelta(days=2),
                is_aggregator=True,
                source="test",
                source_url="https://example.test/recent-2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=2,
                pulse_name="Older Pulse",
                pulse_created=now - timedelta(days=5),
                is_aggregator=False,
                source="test",
                source_url="https://example.test/older",
                fetched_at=now,
            ),
        ]
    )
    test_db.commit()

    def override_get_session():
        yield test_db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/indicator-types", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["types"]) == 2
    assert next(
        item["count"] for item in payload["types"] if item["indicator_type"] == "ip"
    ) == 2
    print("PASS")