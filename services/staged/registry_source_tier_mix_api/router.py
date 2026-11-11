from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_source_tier_mix


router = APIRouter(prefix="/api")


class SourceMix(BaseModel):
    registry_source: str
    total: int
    tiers: dict[str, int]


class SourceTierMixResponse(BaseModel):
    sources: list[SourceMix]
    totals: dict[str, int]


@router.get(
    "/registry/source-tier-mix",
    response_model=SourceTierMixResponse,
    status_code=200,
)
def source_tier_mix(session: Session = Depends(get_session)) -> SourceTierMixResponse:
    return get_source_tier_mix(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    registry_source TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (registry_source, risk_tier)
                VALUES (:registry_source, :risk_tier)
                """
            ),
            [
                {"registry_source": "github", "risk_tier": "high"},
                {"registry_source": "github", "risk_tier": "low"},
                {"registry_source": "npm", "risk_tier": "high"},
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/source-tier-mix")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2

        by_source = {
            source["registry_source"]: source for source in payload["sources"]
        }
        assert by_source["github"]["total"] == 2
        assert by_source["github"]["tiers"] == {"high": 1, "low": 1}
        assert by_source["npm"]["total"] == 1
        assert by_source["npm"]["tiers"] == {"high": 1}
        assert payload["totals"] == {"high": 2, "low": 1}
        assert sum(payload["totals"].values()) == 3

    print("PASS")