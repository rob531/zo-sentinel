from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_indicator_type_breakdown


router = APIRouter(prefix="/api")


class IndicatorTypeBreakdownItem(BaseModel):
    indicator_type: str
    count: int
    distinct_indicators: int
    distinct_pulses: int
    last_fetched_at: Optional[datetime] = None


class IndicatorTypeBreakdownResponse(BaseModel):
    total_refs: int
    types: List[IndicatorTypeBreakdownItem]


def _normalise_breakdown(result: Any) -> Dict[str, Any]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if isinstance(result, dict):
        payload = dict(result)
        rows = payload.get("types", [])
    elif isinstance(result, tuple) and len(result) == 2:
        total_refs, rows = result
        payload = {"total_refs": total_refs, "types": rows}
    else:
        rows = list(result or [])
        payload = {"types": rows}

    normalised_rows = []
    for row in rows or []:
        if hasattr(row, "model_dump"):
            row = row.model_dump()
        elif hasattr(row, "dict"):
            row = row.dict()
        normalised_rows.append(dict(row))

    payload["types"] = normalised_rows
    if "total_refs" not in payload:
        payload["total_refs"] = sum(int(row.get("count", 0)) for row in normalised_rows)
    return payload


@router.get(
    "/threat-intel/indicator-types",
    response_model=IndicatorTypeBreakdownResponse,
)
def indicator_type_breakdown(
    session: Session = Depends(get_session),
) -> IndicatorTypeBreakdownResponse:
    result = get_indicator_type_breakdown(session)
    return IndicatorTypeBreakdownResponse(**_normalise_breakdown(result))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )

    ThreatIntelRef.__table__.create(test_engine)
    test_session = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id="pulse-2",
                pulse_name="Other Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/3",
                fetched_at=now,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/indicator-types")
    response.raise_for_status()
    body = response.json()
    assert body["total_refs"] == 3
    assert len(body["types"]) == 2
    ip_row = next(row for row in body["types"] if row["indicator_type"] == "ip")
    assert ip_row["count"] == 2
    assert ip_row["distinct_indicators"] == 2
    print("PASS")