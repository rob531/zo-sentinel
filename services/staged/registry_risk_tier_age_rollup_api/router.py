from __future__ import annotations

import inspect
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_risk_tier_age_rollup

router = APIRouter(prefix="/api", tags=["registry"])


class RiskTierAgeRollupItem(BaseModel):
    risk_tier: str
    server_count: int
    never_assessed_count: int
    stale_30d_count: int
    stale_90d_count: int
    median_days_since_assessment: Optional[float]
    oldest_last_assessed: Optional[datetime]


class RegistryRiskTierAgeRollupResponse(BaseModel):
    tiers: List[RiskTierAgeRollupItem]
    total_servers: int
    generated_at: datetime


RiskTierAgeRollup = RiskTierAgeRollupItem
RiskTierAgeRollupResponse = RiskTierAgeRollupItem
RiskTierAgeRollupItemResponse = RiskTierAgeRollupItem
RegistryRiskTierAgeRollup = RegistryRiskTierAgeRollupResponse


@router.get(
    "/registry/risk-tier-age-rollup",
    response_model=RegistryRiskTierAgeRollupResponse,
    operation_id="get_registry_risk_tier_age_rollup",
)
async def get_registry_risk_tier_age_rollup_endpoint(
    session: Session = Depends(get_session),
):
    result = get_registry_risk_tier_age_rollup(session)
    if inspect.isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    that_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    url TEXT,
                    confidence REAL,
                    trust_score REAL,
                    first_seen TIMESTAMP,
                    last_assessed TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    meta JSON,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    risk_tier,
                    last_assessed,
                    last_seen,
                    last_scanned,
                    scan_count,
                    registry_source
                ) VALUES
                    (:server_id, :name, :risk_tier, :last_assessed,
                     :last_seen, :last_scanned, :scan_count, :registry_source)
                """
            ),
            [
                {
                    "server_id": "recent-high",
                    "name": "recent-high",
                    "risk_tier": "HIGH_RISK_ISOLATED",
                    "last_assessed": now - timedelta(days=2),
                    "last_seen": now,
                    "last_scanned": now,
                    "scan_count": 3,
                    "registry_source": "test",
                },
                {
                    "server_id": "never-caution",
                    "name": "never-caution",
                    "risk_tier": "CAUTION_LIMITED",
                    "last_assessed": None,
                    "last_seen": now,
                    "last_scanned": None,
                    "scan_count": 0,
                    "registry_source": "test",
                },
                {
                    "server_id": "unknown-one",
                    "name": "unknown-one",
                    "risk_tier": None,
                    "last_assessed": None,
                    "last_seen": now,
                    "last_scanned": None,
                    "scan_count": 0,
                    "registry_source": "test",
                },
                {
                    "server_id": "unknown-two",
                    "name": "unknown-two",
                    "risk_tier": None,
                    "last_assessed": None,
                    "last_seen": now,
                    "last_scanned": None,
                    "scan_count": 0,
                    "registry_source": "test",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/risk-tier-age-rollup")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4
        tiers = {row["risk_tier"]: row for row in payload["tiers"]}
        assert tiers["CAUTION_LIMITED"]["never_assessed_count"] == 1
        assert "UNKNOWN" in tiers

    print("PASS")