from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink

from .logic import get_match_basis_mix

router = APIRouter(prefix="/api")


class MatchBasisAggregate(BaseModel):
    match_basis: str
    link_count: int
    server_count: int
    avg_confidence: float


class ConfidenceBandCount(BaseModel):
    band: str
    count: int


class MatchBasisMixResponse(BaseModel):
    total_links: int
    bases: list[MatchBasisAggregate]
    confidence_bands: list[ConfidenceBandCount]


@router.get("/vuln/links/match-basis", response_model=MatchBasisMixResponse)
def get_vuln_link_match_basis_mix(
    session: Session = Depends(get_session),
) -> MatchBasisMixResponse:
    return get_match_basis_mix(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as db:
        db.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=1,
                    match_basis="exact_version",
                    match_value="1.2.3",
                    match_confidence=0.2,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=2,
                    match_basis="exact_version",
                    match_value="2.0.0",
                    match_confidence=0.5,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    id=3,
                    advisory_id=3,
                    server_id=3,
                    match_basis="heuristic",
                    match_value="3.1.0",
                    match_confidence=0.8,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    id=4,
                    advisory_id=4,
                    server_id=4,
                    match_basis="heuristic",
                    match_value="4.0.0",
                    match_confidence=0.9,
                    linked_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/links/match-basis")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_links"] == 4
        assert len(payload["bases"]) == 2

        exact_version = next(
            item for item in payload["bases"] if item["match_basis"] == "exact_version"
        )
        assert exact_version["link_count"] == 2
        assert abs(exact_version["avg_confidence"] - 0.35) < 1e-9
        assert sum(item["count"] for item in payload["confidence_bands"]) == 4

    print("PASS")