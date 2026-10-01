# deps: fastapi, pydantic, sqlalchemy
"""registry_scoring_summary router.

Provides a high-level summary of the MCP server registry: how many servers have
been scored vs. never-scored, broken down by risk tier and registry source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_scoring_summary"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TierCount(BaseModel):
    tier: str
    count: int


class SourceCount(BaseModel):
    source: str
    count: int


class ScoredServersSummary(BaseModel):
    total: int
    by_tier: Dict[str, int]


class NeverScoredServersSummary(BaseModel):
    total: int
    by_source: Dict[str, int]


class RegistryScoringSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scored: ScoredServersSummary
    never_scored: NeverScoredServersSummary
    generated_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/registry/scoring-summary",
    response_model=RegistryScoringSummaryResponse,
    name="registry_scoring_summary",
)
def registry_scoring_summary(
    db: Session = Depends(get_session),
) -> RegistryScoringSummaryResponse:
    """
    Summary of the MCP server registry split into scored and never-scored
    buckets.

    - **scored**: servers that appear in `mcp_llm_axis_scores`, grouped by
      `risk_tier`.
    - **never_scored**: servers with no axis-score rows, grouped by
      `registry_source`.
    """
    # IDs of servers that have at least one axis score row
    scored_ids_subq = (
        select(McpLlmAxisScore.server_id).distinct().subquery()
    )

    # Scored bucket: risk_tier breakdown
    scored_q = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .filter(McpServerRegistry.server_id.in_(scored_ids_subq))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    scored_by_tier: Dict[str, int] = {}
    for row in scored_q:
        tier = row.risk_tier or "unknown"
        scored_by_tier[tier] = row.cnt

    # Never-scored bucket: registry_source breakdown
    never_scored_q = (
        db.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .filter(~McpServerRegistry.server_id.in_(scored_ids_subq))
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    never_scored_by_source: Dict[str, int] = {}
    for row in never_scored_q:
        source = row.registry_source or "unknown"
        never_scored_by_source[source] = row.cnt

    return RegistryScoringSummaryResponse(
        scored=ScoredServersSummary(
            total=sum(scored_by_tier.values()),
            by_tier=scored_by_tier,
        ),
        never_scored=NeverScoredServersSummary(
            total=sum(never_scored_by_source.values()),
            by_source=never_scored_by_source,
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory SQLite for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Import and create tables using the real model Base
    from app.models import Base

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    with SessionLocal() as db:
        # 4 servers: 2 scored, 2 never-scored
        servers = [
            McpServerRegistry(
                server_id="srv-001",
                name="Alpha",
                risk_tier="TRUSTED_GENERAL",
                registry_source="source-a",
                url="https://alpha.example.com",
                description="scored server",
                confidence=1.0,
                trust_score=0.9,
                verdict="trusted",
                verdict_reasoning="",
                first_seen=datetime.utcnow(),
                last_assessed=datetime.utcnow(),
                last_scanned=datetime.utcnow(),
                last_seen=datetime.utcnow(),
                meta={},
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Beta",
                risk_tier="HIGH_RISK_ISOLATED",
                registry_source="source-b",
                url="https://beta.example.com",
                description="scored high-risk server",
                confidence=1.0,
                trust_score=0.1,
                verdict="untrusted",
                verdict_reasoning="",
                first_seen=datetime.utcnow(),
                last_assessed=datetime.utcnow(),
                last_scanned=datetime.utcnow(),
                last_seen=datetime.utcnow(),
                meta={},
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Gamma",
                risk_tier="STANDARD",
                registry_source="source-a",
                url="https://gamma.example.com",
                description="never scored",
                confidence=0.0,
                trust_score=0.0,
                verdict="",
                verdict_reasoning="",
                first_seen=datetime.utcnow(),
                last_assessed=None,
                last_scanned=None,
                last_seen=datetime.utcnow(),
                meta={},
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="srv-004",
                name="Delta",
                risk_tier="LOW_RISK",
                registry_source="source-c",
                url="https://delta.example.com",
                description="never scored",
                confidence=0.0,
                trust_score=0.0,
                verdict="",
                verdict_reasoning="",
                first_seen=datetime.utcnow(),
                last_assessed=None,
                last_scanned=None,
                last_seen=datetime.utcnow(),
                meta={},
                scan_count=0,
            ),
        ]
        db.add_all(servers)
        db.flush()

        # Two scored servers: srv-001, srv-002
        db.add_all(
            [
                McpLlmAxisScore(
                    server_id="srv-001",
                    axis_name="overall_risk",
                    label="LOW",
                    label_index=0,
                    model_version="m1",
                    decision_rule_version="v1",
                    probs={},
                    p_top=0.85,
                    p_critical=0.0,
                    p_danger=0.05,
                    escalated=False,
                    escalated_to=None,
                    adapter_sha256="sha256_a1",
                    scored_at=datetime.utcnow(),
                ),
                McpLlmAxisScore(
                    server_id="srv-002",
                    axis_name="overall_risk",
                    label="CRITICAL",
                    label_index=3,
                    model_version="m1",
                    decision_rule_version="v1",
                    probs={},
                    p_top=0.95,
                    p_critical=0.9,
                    p_danger=0.05,
                    escalated=True,
                    escalated_to="KNOWN_THREAT",
                    adapter_sha256="sha256_b2",
                    scored_at=datetime.utcnow(),
                ),
            ]
        )
        db.commit()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    # Override using the real FastAPI app instance
    from app.main import app as main_app

    main_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(main_app)

    resp = client.get("/api/registry/scoring-summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    scored = data["scored"]
    never_scored = data["never_scored"]

    # Validate scored bucket
    assert scored["total"] == 2, f"Expected scored.total==2, got {scored['total']}"
    assert (
        "TRUSTED_GENERAL" in scored["by_tier"]
    ), f"TRUSTED_GENERAL missing from by_tier: {scored['by_tier']}"
    assert (
        "HIGH_RISK_ISOLATED" in scored["by_tier"]
    ), f"HIGH_RISK_ISOLATED missing from by_tier: {scored['by_tier']}"
    assert scored["by_tier"]["TRUSTED_GENERAL"] == 1
    assert scored["by_tier"]["HIGH_RISK_ISOLATED"] == 1

    # Validate never-scored bucket
    assert (
        never_scored["total"] == 2
    ), f"Expected never_scored.total==2, got {never_scored['total']}"
    assert (
        "source-a" in never_scored["by_source"]
    ), f"source-a missing from by_source: {never_scored['by_source']}"
    assert (
        "source-c" in never_scored["by_source"]
    ), f"source-c missing from by_source: {never_scored['by_source']}"
    assert never_scored["by_source"]["source-a"] == 1  # only Gamma
    assert never_scored["by_source"]["source-c"] == 1  # only Delta

    # Validate generated_at is ISO-8601
    assert "T" in data["generated_at"], f"generated_at not ISO-8601: {data['generated_at']}"

    print("PASS")
    sys.exit(0)
