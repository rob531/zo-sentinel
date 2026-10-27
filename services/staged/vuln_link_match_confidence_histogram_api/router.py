from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink
from .logic import get_match_confidence_histogram


router = APIRouter(prefix="/api")


class HistogramBin(BaseModel):
    bin_index: int
    lower: float
    upper: float
    count: int


class MatchConfidenceHistogramResponse(BaseModel):
    bins: int
    histogram: list[HistogramBin]
    by_basis: dict[str, dict[str, int | float]]
    total_links: int
    min_confidence: float
    max_confidence: float
    avg_confidence: float


@router.get(
    "/vuln/links/match-confidence-histogram",
    response_model=MatchConfidenceHistogramResponse,
)
def match_confidence_histogram(
    bins: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> MatchConfidenceHistogramResponse:
    result = get_match_confidence_histogram(session, bins)
    return MatchConfidenceHistogramResponse(**result)


def _run_self_test() -> None:
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO vuln_links
                    (advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (:advisory_id, :server_id, :match_basis, :match_value,
                     :match_confidence, :linked_at)
                """
            ),
            [
                {
                    "advisory_id": "ADV-1",
                    "server_id": 1,
                    "match_basis": "exact",
                    "match_value": "CVE-1",
                    "match_confidence": 0.1,
                    "linked_at": now,
                },
                {
                    "advisory_id": "ADV-2",
                    "server_id": 1,
                    "match_basis": "exact",
                    "match_value": "CVE-2",
                    "match_confidence": 0.45,
                    "linked_at": now,
                },
                {
                    "advisory_id": "ADV-3",
                    "server_id": 2,
                    "match_basis": "fuzzy",
                    "match_value": "CVE-3",
                    "match_confidence": 0.7,
                    "linked_at": now,
                },
                {
                    "advisory_id": "ADV-4",
                    "server_id": 2,
                    "match_basis": "fuzzy",
                    "match_value": "CVE-4",
                    "match_confidence": 0.95,
                    "linked_at": now,
                },
            ],
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/vuln/links/match-confidence-histogram",
                params={"bins": 10},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_links"] == 4, payload
            assert sum(bin_data["count"] for bin_data in payload["histogram"]) == 4, payload
            assert payload["max_confidence"] == 0.95, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()