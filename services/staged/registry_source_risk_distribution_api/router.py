from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_source_risk_distribution


class TopRiskServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    trust_score: Optional[float]


class RegistrySourceRiskDistribution(BaseModel):
    registry_source: str
    total_servers: int
    distinct_scanned: int
    avg_trust_score: Optional[float]
    avg_confidence: Optional[float]
    tiers: Dict[str, int] = Field(default_factory=dict)
    verdicts: Dict[str, int] = Field(default_factory=dict)
    top_risk: List[TopRiskServer] = Field(default_factory=list)


class RegistrySourceRiskDistributionTotals(BaseModel):
    servers: int
    sources: int


class RegistrySourceRiskDistributionResponse(BaseModel):
    sources: List[RegistrySourceRiskDistribution] = Field(default_factory=list)
    totals: RegistrySourceRiskDistributionTotals


router = APIRouter(prefix="/api", tags=["registry"])


@router.get(
    "/registry/sources/risk-distribution",
    response_model=RegistrySourceRiskDistributionResponse,
)
def registry_source_risk_distribution(
    session: Session = Depends(get_session),
) -> RegistrySourceRiskDistributionResponse:
    return get_registry_source_risk_distribution(session)


def _self_test() -> None:
    from datetime import datetime, timezone
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as SQLAlchemySession
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
                    registry_source VARCHAR NOT NULL,
                    url VARCHAR,
                    description TEXT,
                    trust_score FLOAT,
                    verdict VARCHAR,
                    verdict_reasoning TEXT,
                    confidence FLOAT,
                    last_assessed DATETIME,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    scan_count INTEGER,
                    risk_tier VARCHAR,
                    meta JSON
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            (
                "server-1",
                "Alpha",
                "npm",
                "https://registry.npmjs.org/alpha",
                "First server",
                0.20,
                "suspicious",
                "Low trust",
                0.30,
                now,
                now,
                now,
                now,
                3,
                "critical",
                json.dumps({"seeded": True}),
            ),
            (
                "server-2",
                "Bravo",
                "npm",
                "https://registry.npmjs.org/bravo",
                "Second server",
                0.60,
                "trusted",
                "Acceptable trust",
                0.80,
                now,
                now,
                now,
                now,
                2,
                "medium",
                json.dumps({"seeded": True}),
            ),
            (
                "server-3",
                "Charlie",
                "pypi",
                "https://pypi.org/project/charlie",
                "Third server",
                0.85,
                "trusted",
                "Strong trust",
                0.90,
                now,
                now,
                now,
                now,
                5,
                "low",
                json.dumps({"seeded": True}),
            ),
            (
                "server-4",
                "Delta",
                "pypi",
                "https://pypi.org/project/delta",
                "Fourth server",
                0.40,
                "suspicious",
                "Review required",
                0.50,
                now,
                now,
                now,
                None,
                0,
                "critical",
                json.dumps({"seeded": True}),
            ),
        ]

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, registry_source, url, description,
                    trust_score, verdict, verdict_reasoning, confidence,
                    last_assessed, first_seen, last_seen, last_scanned,
                    scan_count, risk_tier, meta
                ) VALUES (
                    :server_id, :name, :registry_source, :url, :description,
                    :trust_score, :verdict, :verdict_reasoning, :confidence,
                    :last_assessed, :first_seen, :last_seen, :last_scanned,
                    :scan_count, :risk_tier, :meta
                )
                """
            ),
            [
                {
                    "server_id": row[0],
                    "name": row[1],
                    "registry_source": row[2],
                    "url": row[3],
                    "description": row[4],
                    "trust_score": row[5],
                    "verdict": row[6],
                    "verdict_reasoning": row[7],
                    "confidence": row[8],
                    "last_assessed": row[9],
                    "first_seen": row[10],
                    "last_seen": row[11],
                    "last_scanned": row[12],
                    "scan_count": row[13],
                    "risk_tier": row[14],
                    "meta": row[15],
                }
                for row in rows
            ],
        )

    session_factory = SQLAlchemySession(bind=engine)

    def override_get_session():
        with session_factory as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/sources/risk-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 4
        assert payload["totals"]["sources"] == 2
        assert len(payload["sources"]) == 2

        for source in payload["sources"]:
            assert sum(source["tiers"].values()) == source["total_servers"]
            assert source["top_risk"]

    assert McpServerRegistry.__tablename__ == "mcp_server_registry"
    session_factory.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()