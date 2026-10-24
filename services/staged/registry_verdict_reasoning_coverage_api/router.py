from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_verdict_reasoning_coverage
except ImportError:
    get_verdict_reasoning_coverage = None


router = APIRouter(prefix="/api", tags=["registry"])


class TierCoverage(BaseModel):
    risk_tier: str
    total_servers: int
    with_reasoning_count: int
    reasoning_coverage_pct: float
    avg_confidence: Optional[float]
    null_confidence_count: int


class VerdictCoverage(BaseModel):
    verdict: str
    total_servers: int
    with_reasoning_count: int


class CoverageSummary(BaseModel):
    total_servers: int
    total_with_reasoning: int
    overall_coverage_pct: float


class VerdictReasoningCoverageResponse(BaseModel):
    registry_source: Optional[str]
    tiers: List[TierCoverage]
    verdicts: List[VerdictCoverage]
    summary: CoverageSummary


def _coverage_rows(
    session: Session, registry_source: Optional[str]
) -> List[McpServerRegistry]:
    statement = select(McpServerRegistry)
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )
    return list(session.execute(statement).scalars())


def _has_reasoning(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def _rounded_average(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(round(sum(values) / len(values), 2))


def _build_coverage(
    rows: List[McpServerRegistry], registry_source: Optional[str]
) -> VerdictReasoningCoverageResponse:
    tier_values: Dict[str, List[McpServerRegistry]] = {}
    verdict_values: Dict[str, List[McpServerRegistry]] = {}

    for row in rows:
        tier = str(row.risk_tier) if row.risk_tier is not None else "UNKNOWN"
        verdict = str(row.verdict) if row.verdict is not None else "UNKNOWN"
        tier_values.setdefault(tier, []).append(row)
        verdict_values.setdefault(verdict, []).append(row)

    tiers: List[TierCoverage] = []
    for tier, tier_rows in sorted(tier_values.items()):
        with_reasoning = sum(
            1 for row in tier_rows if _has_reasoning(row.verdict_reasoning)
        )
        confidence_values = [
            float(row.confidence)
            for row in tier_rows
            if row.confidence is not None
        ]
        null_confidence_count = sum(
            1 for row in tier_rows if row.confidence is None
        )
        total = len(tier_rows)
        tiers.append(
            TierCoverage(
                risk_tier=tier,
                total_servers=total,
                with_reasoning_count=with_reasoning,
                reasoning_coverage_pct=float(
                    round((with_reasoning / total) * 100, 2) if total else 0.0
                ),
                avg_confidence=_rounded_average(confidence_values),
                null_confidence_count=null_confidence_count,
            )
        )

    verdicts: List[VerdictCoverage] = []
    for verdict, verdict_rows in sorted(verdict_values.items()):
        with_reasoning = sum(
            1 for row in verdict_rows if _has_reasoning(row.verdict_reasoning)
        )
        verdicts.append(
            VerdictCoverage(
                verdict=verdict,
                total_servers=len(verdict_rows),
                with_reasoning_count=with_reasoning,
            )
        )

    total_servers = len(rows)
    total_with_reasoning = sum(
        1 for row in rows if _has_reasoning(row.verdict_reasoning)
    )
    summary = CoverageSummary(
        total_servers=total_servers,
        total_with_reasoning=total_with_reasoning,
        overall_coverage_pct=float(
            round(
                (total_with_reasoning / total_servers) * 100
                if total_servers
                else 0.0,
                2,
            )
        ),
    )

    return VerdictReasoningCoverageResponse(
        registry_source=registry_source,
        tiers=tiers,
        verdicts=verdicts,
        summary=summary,
    )


@router.get(
    "/registry/verdict-reasoning-coverage",
    response_model=VerdictReasoningCoverageResponse,
)
def get_registry_verdict_reasoning_coverage(
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> VerdictReasoningCoverageResponse:
    rows = _coverage_rows(session, registry_source)
    return _build_coverage(rows, registry_source)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    seed_rows = [
        {
            "server_id": "one",
            "name": "one",
            "registry_source": "github",
            "verdict": "SAFE",
            "verdict_reasoning": "Reviewed and passed all checks.",
            "confidence": 0.9,
            "risk_tier": "LOW",
            "last_assessed": None,
        },
        {
            "server_id": "two",
            "name": "two",
            "registry_source": "github",
            "verdict": "SAFE",
            "verdict_reasoning": "",
            "confidence": 0.8,
            "risk_tier": "HIGH",
            "last_assessed": None,
        },
        {
            "server_id": "three",
            "name": "three",
            "registry_source": "github",
            "verdict": "REVIEW",
            "verdict_reasoning": "Some uncertainty remains.",
            "confidence": None,
            "risk_tier": "HIGH",
            "last_assessed": None,
        },
        {
            "server_id": "four",
            "name": "four",
            "registry_source": "github",
            "verdict": "REVIEW",
            "verdict_reasoning": "Reviewed with supporting evidence.",
            "confidence": 0.7,
            "risk_tier": "LOW",
            "last_assessed": None,
        },
    ]

    with TestSession() as session:
        for row in seed_rows:
            session.add(McpServerRegistry(**row))
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/verdict-reasoning-coverage"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_servers"] == len(seed_rows)
    assert sum(
        1 for tier in payload["tiers"] if tier["null_confidence_count"] == 1
    ) == 1
    assert 0 <= payload["summary"]["overall_coverage_pct"] <= 100
    print("PASS")