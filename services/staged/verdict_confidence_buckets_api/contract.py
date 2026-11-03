from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ConfidenceBucket(BaseModel):
    band: str
    count: int
    verdicts: Dict[str, int]
    sources: Dict[str, int]


class ConfidenceBucketsResponse(BaseModel):
    total: int
    bands: List[ConfidenceBucket]
    generated_at: datetime


@router.get(
    "/verdict/confidence-buckets",
    response_model=ConfidenceBucketsResponse,
)
def get_confidence_buckets(
    registry_source: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> ConfidenceBucketsResponse:
    band_expression = case(
        (McpServerRegistry.confidence >= 0.9, "high"),
        (McpServerRegistry.confidence >= 0.7, "medium"),
        (McpServerRegistry.confidence >= 0.5, "low"),
        else_="minimal",
    )
    verdict_expression = func.coalesce(
        func.nullif(McpServerRegistry.verdict, ""), "unknown"
    )
    source_expression = func.coalesce(
        func.nullif(McpServerRegistry.registry_source, ""), "unknown"
    )

    statement = select(
        band_expression.label("band"),
        verdict_expression.label("verdict"),
        source_expression.label("source"),
    ).select_from(McpServerRegistry)

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).mappings().all()

    band_order = ("high", "medium", "low", "minimal")
    buckets = {
        band: {"count": 0, "verdicts": {}, "sources": {}}
        for band in band_order
    }

    for row in rows:
        band = row["band"] or "minimal"
        verdict = row["verdict"] or "unknown"
        source = row["source"] or "unknown"

        bucket = buckets.setdefault(
            band, {"count": 0, "verdicts": {}, "sources": {}}
        )
        bucket["count"] += 1
        bucket["verdicts"][verdict] = bucket["verdicts"].get(verdict, 0) + 1
        bucket["sources"][source] = bucket["sources"].get(source, 0) + 1

    return ConfidenceBucketsResponse(
        total=len(rows),
        bands=[
            ConfidenceBucket(
                band=band,
                count=buckets[band]["count"],
                verdicts=buckets[band]["verdicts"],
                sources=buckets[band]["sources"],
            )
            for band in band_order
        ],
        generated_at=datetime.now(timezone.utc),
    )


app = FastAPI(title="Verdict Confidence Buckets API")
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY NOT NULL,
                    name VARCHAR,
                    registry_source VARCHAR,
                    trust_score FLOAT,
                    verdict VARCHAR,
                    verdict_reasoning TEXT,
                    confidence FLOAT,
                    risk_tier VARCHAR,
                    last_assessed DATETIME,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    last_scanned DATETIME,
                    scan_count INTEGER DEFAULT 0,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, trust_score, verdict,
                     confidence, risk_tier)
                VALUES
                    (:server_id, :name, :registry_source, :trust_score, :verdict,
                     :confidence, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "confidence-1",
                    "name": "confidence one",
                    "registry_source": "source-a",
                    "trust_score": 0.95,
                    "verdict": "trusted",
                    "confidence": 0.95,
                    "risk_tier": "low",
                },
                {
                    "server_id": "confidence-2",
                    "name": "confidence two",
                    "registry_source": "source-a",
                    "trust_score": 0.75,
                    "verdict": "review",
                    "confidence": 0.75,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "confidence-3",
                    "name": "confidence three",
                    "registry_source": "source-b",
                    "trust_score": 0.60,
                    "verdict": "trusted",
                    "confidence": 0.60,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "confidence-4",
                    "name": "confidence four",
                    "registry_source": "source-b",
                    "trust_score": 0.20,
                    "verdict": "review",
                    "confidence": 0.20,
                    "risk_tier": "high",
                },
            ],
        )

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/verdict/confidence-buckets")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert sum(bucket["count"] for bucket in payload["bands"]) == 4

        verdict_counts = {}
        for bucket in payload["bands"]:
            for verdict, count in bucket["verdicts"].items():
                verdict_counts[verdict] = verdict_counts.get(verdict, 0) + count

        assert verdict_counts == {"trusted": 2, "review": 2}

    print("PASS")


if __name__ == "__main__":
    _run_self_test()