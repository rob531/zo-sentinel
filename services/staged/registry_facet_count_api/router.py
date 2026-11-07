from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_facet_counts
except ImportError:
    try:
        from .logic import get_facet_counts as get_registry_facet_counts
    except ImportError:
        from .logic import registry_facet_counts as get_registry_facet_counts

router = APIRouter(prefix="/api", tags=["registry"])


class FacetValueCount(BaseModel):
    value: str | None
    count: int


class UnscoredCounts(BaseModel):
    missing_trust_score: int
    missing_last_assessed: int


class RegistryFacetCountResponse(BaseModel):
    total_servers: int
    by_registry_source: list[FacetValueCount]
    by_risk_tier: list[FacetValueCount]
    by_verdict: list[FacetValueCount]
    unscored: UnscoredCounts
    computed_at: datetime


@router.get(
    "/registry/facets",
    response_model=RegistryFacetCountResponse,
)
def get_registry_facets(
    db: Session = Depends(get_session),
) -> RegistryFacetCountResponse:
    return RegistryFacetCountResponse(**get_registry_facet_counts(db))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SQLASession
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)

    with SQLASession(test_engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="facet-test-1",
                    name="Facet Test 1",
                    registry_source="github",
                    risk_tier="high",
                    verdict="verified",
                    trust_score=90,
                    last_assessed=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="facet-test-2",
                    name="Facet Test 2",
                    registry_source="github",
                    risk_tier="high",
                    verdict="verified",
                    trust_score=80,
                    last_assessed=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="facet-test-3",
                    name="Facet Test 3",
                    registry_source="registry",
                    risk_tier="low",
                    verdict="pending",
                    trust_score=70,
                    last_assessed=datetime.utcnow(),
                ),
                McpServerRegistry(
                    server_id="facet-test-4",
                    name="Facet Test 4",
                    registry_source="registry",
                    risk_tier="medium",
                    verdict="rejected",
                    trust_score=None,
                    last_assessed=datetime.utcnow(),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SQLASession(test_engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/facets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert sum(1 for bucket in payload["by_risk_tier"] if bucket["count"] == 2) == 1
    assert payload["unscored"]["missing_trust_score"] == 1
    print("PASS")