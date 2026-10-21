from typing import Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from .logic import host_rollup


router = APIRouter(prefix="/api/registry", tags=["registry"])


class RiskTierCounts(BaseModel):
    HIGH_RISK_ISOLATED: int = 0
    CAUTION_LIMITED: int = 0
    ENTERPRISE_CONTROLLED: int = 0
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    KNOWN_THREAT: int = 0
    INSUFFICIENT: int = 0


class RegistryHost(BaseModel):
    host: str
    server_count: int
    tiers: RiskTierCounts
    avg_trust_score: float


class RegistryHostRollup(BaseModel):
    total_hosts: int
    servers: list[RegistryHost]


@router.get("/hosts", response_model=RegistryHostRollup)
def get_registry_hosts(
    limit: int = Query(default=50, ge=1, le=200),
    db_session=Depends(get_session),
):
    return host_rollup(db_session, limit=limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    test_session.execute(
        McpServerRegistry.__table__.insert(),
        [
            {
                "server_id": "host-a-1",
                "name": "Host A Server 1",
                "url": "https://a.example/mcp/1",
                "registry_source": "test",
                "trust_score": 90.0,
                "risk_tier": "TRUSTED_GENERAL",
                "verdict": "ACCEPT",
            },
            {
                "server_id": "host-a-2",
                "name": "Host A Server 2",
                "url": "https://a.example/mcp/2",
                "registry_source": "test",
                "trust_score": 80.0,
                "risk_tier": "CAUTION_LIMITED",
                "verdict": "REVIEW",
            },
            {
                "server_id": "host-a-3",
                "name": "Host A Server 3",
                "url": "https://a.example/mcp/3",
                "registry_source": "test",
                "trust_score": 70.0,
                "risk_tier": "TRUSTED_GENERAL",
                "verdict": "ACCEPT",
            },
            {
                "server_id": "host-b-1",
                "name": "Host B Server 1",
                "url": "https://b.example/mcp/1",
                "registry_source": "test",
                "trust_score": 60.0,
                "risk_tier": "HIGH_RISK_ISOLATED",
                "verdict": "REVIEW",
            },
        ],
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/hosts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_hosts"] == 2
    assert payload["servers"][0]["server_count"] == 3
    assert set(payload["servers"][0]["tiers"]) == {
        "HIGH_RISK_ISOLATED",
        "CAUTION_LIMITED",
        "ENTERPRISE_CONTROLLED",
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    }
    print("PASS")