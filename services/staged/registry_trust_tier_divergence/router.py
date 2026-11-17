"""HTTP surface for registry trust-tier divergence reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_trust_tier_divergence

router = APIRouter(prefix="/api", tags=["registry_trust_tier_divergence"])


class TrustTierDivergenceServer(BaseModel):
    server_id: str
    name: str | None = None
    trust_score: float | None = None
    risk_tier: str | None = None
    expected_band: str
    divergent: bool


class TrustTierDivergenceResponse(BaseModel):
    servers: list[TrustTierDivergenceServer]
    total: int
    divergent_count: int
    threshold: float


@router.get(
    "/registry/trust-tier-divergence",
    response_model=TrustTierDivergenceResponse,
)
def get_registry_trust_tier_divergence(
    db: Session = Depends(get_session),
) -> TrustTierDivergenceResponse:
    result = get_trust_tier_divergence(db)
    return TrustTierDivergenceResponse(**result)


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="high-divergent",
                    name="High trust, critical tier",
                    registry_source="test",
                    url="https://example.test/high",
                    trust_score=90,
                    confidence=0.99,
                    risk_tier="critical",
                ),
                McpServerRegistry(
                    server_id="consistent",
                    name="Consistent server",
                    registry_source="test",
                    url="https://example.test/consistent",
                    trust_score=60,
                    confidence=0.99,
                    risk_tier="caution",
                ),
                McpServerRegistry(
                    server_id="low-divergent",
                    name="Low trust, trusted tier",
                    registry_source="test",
                    url="https://example.test/low",
                    trust_score=10,
                    confidence=0.99,
                    risk_tier="trusted",
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/trust-tier-divergence")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["divergent_count"] == 2
        assert any(
            server["server_id"] == "high-divergent" and server["divergent"] is True
            for server in payload["servers"]
        )

    print("PASS")