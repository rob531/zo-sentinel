from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_risk_tier_null_share

router = APIRouter(prefix="/api", tags=["registry"])


class DimensionValue(BaseModel):
    value: str
    count: int
    share_pct: float


class RiskTierSourceCount(BaseModel):
    risk_tier: str
    registry_source: str
    count: int


class RegistryRiskTierCoverageResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    dimensions: Dict[str, List[DimensionValue]]
    risk_tier_by_source: List[RiskTierSourceCount]


@router.get(
    "/registry/risk-tier-coverage",
    response_model=RegistryRiskTierCoverageResponse,
)
async def registry_risk_tier_coverage(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = get_registry_risk_tier_null_share(session)
    if isawaitable(result):
        result = await result
    return result


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
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    registry_source VARCHAR,
                    trust_score FLOAT,
                    verdict VARCHAR,
                    confidence FLOAT,
                    last_assessed DATETIME,
                    risk_tier VARCHAR,
                    meta JSON,
                    description VARCHAR,
                    first_seen DATETIME,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    scan_count INTEGER,
                    url VARCHAR,
                    verdict_reasoning VARCHAR
                )
                """
            )
        )
        rows = [
            ("registry-1", "Registry One", "github", 0.91, "safe", 0.95, "low"),
            ("registry-2", "Registry Two", "github", 0.84, "safe", 0.88, "low"),
            ("registry-3", "Registry Three", "npm", 0.62, "unsafe", 0.72, "medium"),
            ("registry-4", "Registry Four", "npm", 0.41, None, 0.55, None),
            ("registry-5", "Registry Five", "npm", 0.33, "", 0.48, None),
            ("registry-6", "Registry Six", "github", 0.78, "safe", 0.82, "high"),
        ]
        for server_id, name, source, trust_score, verdict, confidence, risk_tier in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id,
                        name,
                        registry_source,
                        trust_score,
                        verdict,
                        confidence,
                        risk_tier
                    ) VALUES (
                        :server_id,
                        :name,
                        :registry_source,
                        :trust_score,
                        :verdict,
                        :confidence,
                        :risk_tier
                    )
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "registry_source": source,
                    "trust_score": trust_score,
                    "verdict": verdict,
                    "confidence": confidence,
                    "risk_tier": risk_tier,
                },
            )

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/risk-tier-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 6
        assert any(
            item["value"] == "null" and item["count"] >= 1
            for item in payload["dimensions"]["risk_tier"]
        )
        for values in payload["dimensions"].values():
            assert abs(sum(item["share_pct"] for item in values) - 100.0) < 0.02

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()