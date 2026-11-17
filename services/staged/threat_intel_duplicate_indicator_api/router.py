from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_duplicate_indicators


router = APIRouter(prefix="/api")


class DuplicateIndicator(BaseModel):
    indicator_value: str
    indicator_type: str
    occurrences: int
    source_count: int
    pulse_count: int
    has_aggregator: bool


class DuplicateIndicatorsResponse(BaseModel):
    duplicates: list[DuplicateIndicator]
    duplicate_indicator_count: int
    duplicate_row_count: int


@router.get(
    "/threat-intel/duplicate-indicators",
    response_model=DuplicateIndicatorsResponse,
)
def duplicate_indicators(session: Session = Depends(get_session)):
    return get_duplicate_indicators(session)


if __name__ == "__main__":
    test_engine = create_engine("sqlite://")
    ThreatIntelRef.__table__.create(test_engine)

    from sqlalchemy.orm import sessionmaker

    test_session_factory = sessionmaker(bind=test_engine)
    test_session = test_session_factory()

    test_session.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.10",
                pulse_id=1,
                pulse_name="Pulse One",
                source="source-a",
                is_aggregator=False,
                fetched_at=datetime.now(timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="198.51.100.10",
                pulse_id=2,
                pulse_name="Pulse Two",
                source="source-b",
                is_aggregator=True,
                fetched_at=datetime.now(timezone.utc),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=3,
                pulse_name="Pulse Three",
                source="source-c",
                is_aggregator=False,
                fetched_at=datetime.now(timezone.utc),
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/threat-intel/duplicate-indicators"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["duplicate_indicator_count"] == 1
    assert payload["duplicate_row_count"] == 2
    assert len(payload["duplicates"]) == 1
    duplicate = payload["duplicates"][0]
    assert duplicate["occurrences"] == 2
    assert duplicate["source_count"] == 2
    print("PASS")