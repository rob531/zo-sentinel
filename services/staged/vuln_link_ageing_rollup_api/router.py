from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink
from .logic import get_ageing_rollup


router = APIRouter(prefix="/api", tags=["vulnerability-links"])


class AgeBandCount(BaseModel):
    band: str
    count: int


class MatchBasisCount(BaseModel):
    match_basis: str
    count: int


class AgeingRollupResponse(BaseModel):
    days: int
    total_links: int
    bands: list[AgeBandCount]
    by_match_basis: list[MatchBasisCount]


@router.get("/vuln/links/ageing", response_model=AgeingRollupResponse)
def get_ageing(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> AgeingRollupResponse:
    result = get_ageing_rollup(session, days)
    payload = dict(result) if isinstance(result, dict) else result
    payload.setdefault("days", days)
    return AgeingRollupResponse.model_validate(payload)


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    rows = [
        {
            "advisory_id": 1,
            "server_id": 1,
            "match_basis": "cve",
            "match_value": "CVE-2025-0001",
            "match_confidence": 0.99,
            "linked_at": now - timedelta(days=3),
        },
        {
            "advisory_id": 2,
            "server_id": 1,
            "match_basis": "package",
            "match_value": "example-package",
            "match_confidence": 0.95,
            "linked_at": now - timedelta(days=15),
        },
        {
            "advisory_id": 3,
            "server_id": 1,
            "match_basis": "cve",
            "match_value": "CVE-2025-0003",
            "match_confidence": 0.90,
            "linked_at": now - timedelta(days=60),
        },
        {
            "advisory_id": 4,
            "server_id": 1,
            "match_basis": "hostname",
            "match_value": "host-4",
            "match_confidence": 0.85,
            "linked_at": now - timedelta(days=120),
        },
        {
            "advisory_id": 5,
            "server_id": 1,
            "match_basis": "package",
            "match_value": "old-package",
            "match_confidence": 0.80,
            "linked_at": now - timedelta(days=200),
        },
    ]

    with TestSession() as session:
        session.execute(VulnLink.__table__.insert(), rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/ageing", params={"days": 365})
        assert response.status_code == 200
        body = response.json()
        assert body["total_links"] == 5
        assert any(
            band["band"] == ">90d" and band["count"] >= 1
            for band in body["bands"]
        )

    print("PASS")