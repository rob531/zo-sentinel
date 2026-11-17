from __future__ import annotations

from collections.abc import Generator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_registry_source_verdict_crosstab

router = APIRouter(prefix="/api")


class RegistrySourceCrosstabEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    registry_source: str
    total: int
    verdicts: dict[str, int]
    risk_tiers: dict[str, int]
    avg_confidence: float | None
    scanned_pct: float


class RegistrySourceVerdictCrosstabResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    sources: list[RegistrySourceCrosstabEntry]


@router.get(
    "/registry/source-verdict-crosstab",
    response_model=RegistrySourceVerdictCrosstabResponse,
)
def source_verdict_crosstab(
    session: Session = Depends(get_session),
) -> RegistrySourceVerdictCrosstabResponse:
    payload = get_registry_source_verdict_crosstab(session)
    return RegistrySourceVerdictCrosstabResponse.model_validate(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    confidence REAL,
                    description TEXT,
                    first_seen TIMESTAMP,
                    last_assessed TIMESTAMP,
                    last_scanned TIMESTAMP,
                    last_seen TIMESTAMP,
                    meta JSON,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT,
                    scan_count INTEGER,
                    server_id TEXT,
                    trust_score REAL,
                    url TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )

        rows = [
            (
                "alpha-1",
                "alpha",
                "SAFE",
                "LOW",
                0.80,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
            (
                "alpha-2",
                "alpha",
                "RISKY",
                "HIGH",
                0.60,
                "2026-01-01 00:00:00",
                None,
            ),
            (
                "alpha-3",
                "alpha",
                None,
                "LOW",
                0.70,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
            (
                "beta-1",
                "beta",
                "SAFE",
                "LOW",
                0.90,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
            (
                "beta-2",
                "beta",
                "RISKY",
                "HIGH",
                0.50,
                "2026-01-01 00:00:00",
                None,
            ),
            (
                "gamma-1",
                "gamma",
                "SAFE",
                "MEDIUM",
                0.75,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
            (
                "gamma-2",
                "gamma",
                "RISKY",
                "HIGH",
                0.40,
                "2026-01-01 00:00:00",
                None,
            ),
        ]
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    registry_source,
                    verdict,
                    risk_tier,
                    confidence,
                    first_seen,
                    last_scanned
                ) VALUES (
                    :server_id,
                    :registry_source,
                    :verdict,
                    :risk_tier,
                    :confidence,
                    :first_seen,
                    :last_scanned
                )
                """
            ),
            [
                {
                    "server_id": row[0],
                    "registry_source": row[1],
                    "verdict": row[2],
                    "risk_tier": row[3],
                    "confidence": row[4],
                    "first_seen": row[5],
                    "last_scanned": row[6],
                }
                for row in rows
            ],
        )

    def override_get_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/source-verdict-crosstab")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["sources"]) == 3

        alpha = next(
            source
            for source in payload["sources"]
            if source["registry_source"] == "alpha"
        )
        assert alpha["total"] == 3
        assert alpha["verdicts"].get("SAFE") == 1
        assert alpha["verdicts"].get("UNKNOWN") == 1
        assert alpha["risk_tiers"].get("LOW") == 2
        assert alpha["risk_tiers"].get("HIGH") == 1

    print("PASS")