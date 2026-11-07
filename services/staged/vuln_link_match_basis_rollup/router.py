"""HTTP surface for the vulnerability link match-basis rollup service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_match_basis_rollup

router = APIRouter(prefix="/api/vuln/links", tags=["vuln-links"])


class MatchBasisBucket(BaseModel):
    match_basis: str
    links: int
    distinct_servers: int
    distinct_advisories: int
    avg_confidence: float
    min_confidence: float


class MatchBasisRollupResponse(BaseModel):
    min_confidence: float
    total_links: int
    bases: list[MatchBasisBucket]
    top_match_value: str | None = None


@router.get("/match-basis", response_model=MatchBasisRollupResponse)
def get_match_basis(
    min_confidence: float = 0.0,
    db: Session = Depends(get_session),
) -> MatchBasisRollupResponse:
    result = get_match_basis_rollup(db, min_confidence)

    if isinstance(result, tuple):
        result, top_match_value = result
    else:
        top_match_value = None

    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("min_confidence", min_confidence)
        payload.setdefault("total_links", 0)
        payload.setdefault("bases", [])
        if payload.get("top_match_value") is not None:
            top_match_value = payload["top_match_value"]
    else:
        payload = {
            "min_confidence": min_confidence,
            "total_links": 0,
            "bases": result or [],
        }

    if top_match_value is not None:
        payload["top_match_value"] = top_match_value

    return MatchBasisRollupResponse(**payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import VulnLink

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as session:
        session.add_all(
            [
                VulnLink(
                    advisory_id=101,
                    server_id=1,
                    match_basis="package_name",
                    match_value="requests",
                    match_confidence=0.8,
                ),
                VulnLink(
                    advisory_id=102,
                    server_id=1,
                    match_basis="package_name",
                    match_value="urllib3",
                    match_confidence=0.6,
                ),
                VulnLink(
                    advisory_id=103,
                    server_id=2,
                    match_basis="cpe",
                    match_value="cpe:/a:example:app",
                    match_confidence=0.9,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/vuln/links/match-basis",
        params={"min_confidence": 0.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_links"] == 3
    assert len(body["bases"]) == 2
    assert body["top_match_value"] == "requests"
    print("PASS")