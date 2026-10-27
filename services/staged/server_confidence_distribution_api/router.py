from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_confidence_distribution as _get_confidence_distribution


router = APIRouter(prefix="/api", tags=["server-confidence-distribution"])


class ConfidenceBucket(BaseModel):
    label: str
    count: int = Field(ge=0)


class VerdictConfidence(BaseModel):
    count: int = Field(ge=0)
    mean_confidence: float | None = None
    median_confidence: float | None = None


class ConfidenceDistributionResponse(BaseModel):
    buckets: list[ConfidenceBucket]
    null_count: int = Field(ge=0)
    by_verdict: dict[str, VerdictConfidence]
    server_count: int = Field(ge=0)


@router.get(
    "/servers/confidence/distribution",
    response_model=ConfidenceDistributionResponse,
)
def confidence_distribution(
    bucket: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ConfidenceDistributionResponse:
    return ConfidenceDistributionResponse(
        **_get_confidence_distribution(session, bucket)
    )


if __name__ == "__main__":
    from datetime import datetime

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
    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    confidence FLOAT,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, verdict, risk_tier, confidence, last_assessed)
                VALUES
                    (:server_id, :verdict, :risk_tier, :confidence, :last_assessed)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "LOW",
                    "confidence": 5.0,
                    "last_assessed": datetime(2025, 1, 1),
                },
                {
                    "server_id": "server-2",
                    "verdict": "TRUSTED_RESEARCH",
                    "risk_tier": "MEDIUM",
                    "confidence": 45.0,
                    "last_assessed": datetime(2025, 1, 2),
                },
                {
                    "server_id": "server-3",
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "LOW",
                    "confidence": 72.0,
                    "last_assessed": datetime(2025, 1, 3),
                },
                {
                    "server_id": "server-4",
                    "verdict": "TRUSTED_RESEARCH",
                    "risk_tier": "MEDIUM",
                    "confidence": 95.0,
                    "last_assessed": datetime(2025, 1, 4),
                },
                {
                    "server_id": "server-5",
                    "verdict": "CAUTION_LIMITED",
                    "risk_tier": "HIGH",
                    "confidence": None,
                    "last_assessed": datetime(2025, 1, 5),
                },
            ],
        )

    test_session = test_session_factory()
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: test_session

    try:
        response = TestClient(that_app).get(
            "/api/servers/confidence/distribution",
            params={"bucket": 10},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["null_count"] == 1, payload
        assert payload["server_count"] == 5, payload
        assert sum(item["count"] for item in payload["buckets"]) == 4, payload

        bucket_counts = {item["label"]: item["count"] for item in payload["buckets"]}
        assert bucket_counts["0-9"] == 1, payload
        assert bucket_counts["10-19"] == 0, payload
        assert bucket_counts["40-49"] == 1, payload
        assert bucket_counts["70-79"] == 1, payload
        assert bucket_counts["90-99"] == 1, payload

        general = payload["by_verdict"]["TRUSTED_GENERAL"]
        assert general["count"] == 2, payload
        assert abs(general["mean_confidence"] - 38.5) <= 1e-6, payload
        assert abs(general["median_confidence"] - 38.5) <= 1e-6, payload
    finally:
        test_session.close()
        engine.dispose()

    print("PASS")