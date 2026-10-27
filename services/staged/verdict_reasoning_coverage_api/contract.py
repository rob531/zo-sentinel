import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class VerdictCoverage(BaseModel):
    verdict: str
    total: int
    with_reasoning: int


class ConfidenceDistribution(BaseModel):
    min: float
    median: float
    max: float


class MissingSample(BaseModel):
    server_id: str
    name: str
    verdict: str


class VerdictReasoningCoverageResponse(BaseModel):
    total: int
    with_reasoning: int
    without_reasoning: int
    by_verdict: List[VerdictCoverage]
    confidence: ConfidenceDistribution
    missing_samples: List[MissingSample]


router = APIRouter(prefix="/registry", tags=["registry"])


@router.get(
    "/verdict-reasoning-coverage",
    response_model=VerdictReasoningCoverageResponse,
)
def verdict_reasoning_coverage(
    db: Session = Depends(get_session),
) -> VerdictReasoningCoverageResponse:
    rows = list(db.execute(select(McpServerRegistry)).scalars())

    by_verdict: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "with_reasoning": 0}
    )
    confidence_values = []
    missing_samples = []

    for row in rows:
        verdict = str(row.verdict) if row.verdict is not None else "unknown"
        reasoning = row.verdict_reasoning
        has_reasoning = bool(reasoning and str(reasoning).strip())

        by_verdict[verdict]["total"] += 1
        if has_reasoning:
            by_verdict[verdict]["with_reasoning"] += 1

        if row.confidence is not None:
            confidence_values.append(float(row.confidence))

        if row.verdict is not None and row.confidence is None:
            missing_samples.append(
                MissingSample(
                    server_id=str(row.server_id),
                    name=str(row.name or ""),
                    verdict=str(row.verdict),
                )
            )

    with_reasoning = sum(
        1
        for row in rows
        if row.verdict_reasoning and str(row.verdict_reasoning).strip()
    )
    total = len(rows)

    if confidence_values:
        confidence = ConfidenceDistribution(
            min=float(min(confidence_values)),
            median=float(statistics.median(confidence_values)),
            max=float(max(confidence_values)),
        )
    else:
        confidence = ConfidenceDistribution(min=0.0, median=0.0, max=0.0)

    return VerdictReasoningCoverageResponse(
        total=total,
        with_reasoning=with_reasoning,
        without_reasoning=total - with_reasoning,
        by_verdict=[
            VerdictCoverage(
                verdict=verdict,
                total=counts["total"],
                with_reasoning=counts["with_reasoning"],
            )
            for verdict, counts in sorted(by_verdict.items())
        ],
        confidence=confidence,
        missing_samples=missing_samples[:10],
    )


app = FastAPI(title="Verdict Reasoning Coverage API")
app.include_router(router, prefix="/api")


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    seed_rows = [
        {
            "server_id": "server-1",
            "name": "Server One",
            "verdict": "safe",
            "verdict_reasoning": "Reviewed and approved",
            "confidence": 0.92,
            "risk_tier": "low",
            "trust_score": 0.88,
            "last_assessed": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
        {
            "server_id": "server-2",
            "name": "Server Two",
            "verdict": "warning",
            "verdict_reasoning": "Limited evidence",
            "confidence": 0.68,
            "risk_tier": "medium",
            "trust_score": 0.63,
            "last_assessed": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
        {
            "server_id": "server-3",
            "name": "Server Three",
            "verdict": "critical",
            "verdict_reasoning": None,
            "confidence": None,
            "risk_tier": "high",
            "trust_score": 0.22,
            "last_assessed": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
        {
            "server_id": "server-4",
            "name": "Server Four",
            "verdict": "safe",
            "verdict_reasoning": "",
            "confidence": 0.81,
            "risk_tier": "low",
            "trust_score": 0.79,
            "last_assessed": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
        {
            "server_id": "server-5",
            "name": "Server Five",
            "verdict": "warning",
            "verdict_reasoning": None,
            "confidence": 0.55,
            "risk_tier": "medium",
            "trust_score": 0.51,
            "last_assessed": datetime(2024, 1, 1, 12, 0, 0),
            "last_seen": datetime(2024, 1, 2, 12, 0, 0),
        },
    ]

    db = TestingSessionLocal()
    try:
        db.add_all(McpServerRegistry(**row) for row in seed_rows)
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/registry/verdict-reasoning-coverage")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5
    assert payload["with_reasoning"] == 2
    assert payload["without_reasoning"] == 3
    assert len(payload["missing_samples"]) >= 1
    assert payload["missing_samples"][0]["server_id"] == "server-3"
    assert payload["confidence"]["min"] == 0.55
    assert payload["confidence"]["max"] == 0.92
    assert math.isclose(payload["confidence"]["median"], 0.745)

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()