from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_family_risk_rollup as fetch_family_risk_rollup


class RiskTierCounts(BaseModel):
    HIGH: int
    MEDIUM: int
    LOW: int
    UNKNOWN: int


class FamilyRiskRollupItem(BaseModel):
    canonical_family: str
    server_count: int
    risk_tier_counts: RiskTierCounts
    avg_trust_score: float | None
    worst_server_id: str | None


class FamilyRiskRollupResponse(BaseModel):
    total_families: int
    families: list[FamilyRiskRollupItem]


router = APIRouter()


@router.get(
    "/registry/family-risk-rollup",
    response_model=FamilyRiskRollupResponse,
    response_model_exclude_none=False,
)
def get_family_risk_rollup(
    limit: int = Query(25),
    min_servers: int = Query(1),
    session: Session = Depends(get_session),
) -> FamilyRiskRollupResponse:
    limit = max(1, min(limit, 200))
    min_servers = max(1, min(min_servers, 1000))
    return fetch_family_risk_rollup(
        session=session,
        limit=limit,
        min_servers=min_servers,
    )


def _self_test() -> None:
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    canonical_family TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    name TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, canonical_family, risk_tier, trust_score, name, verdict)
                VALUES
                    ('srv-1', 'acme/http-client', 'HIGH', 0.7, 'HTTP Client 1', 'BLOCK'),
                    ('srv-2', 'acme/http-client', 'LOW', 0.9, 'HTTP Client 2', 'ALLOW'),
                    ('srv-3', 'acme/db-driver', 'MEDIUM', 0.8, 'DB Driver', 'REVIEW'),
                    ('srv-4', NULL, 'UNKNOWN', NULL, 'Uncategorised', NULL)
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/family-risk-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_families"] == 3
        assert payload["families"][0]["canonical_family"] == "acme/http-client"
        assert payload["families"][0]["server_count"] == 2
        assert payload["families"][0]["risk_tier_counts"]["HIGH"] == 1
        assert payload["families"][0]["risk_tier_counts"]["LOW"] == 1
        assert isinstance(payload["families"][0]["avg_trust_score"], float)

        limited = client.get(
            "/api/registry/family-risk-rollup",
            params={"limit": 1},
        )
        assert limited.status_code == 200
        assert len(limited.json()["families"]) == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()