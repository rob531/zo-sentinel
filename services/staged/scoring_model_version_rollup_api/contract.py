# services/staged/scoring_model_version_rollup_api/contract.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/scoring", tags=["scoring_model_version_rollup"])


class VersionInfo(BaseModel):
    model_version: str = Field(..., description="Scoring model version")
    decision_rule_version: str = Field(..., description="Decision rule version")
    adapter_sha256: str = Field(..., description="Adapter SHA256")
    score_count: int = Field(..., description="Number of scores")
    distinct_server_count: int = Field(..., description="Distinct server count")
    distinct_axis_count: int = Field(..., description="Distinct axis count")
    overall_risk_count: int = Field(..., description="Rows where axis_name == 'overall_risk'")
    first_scored_at: datetime = Field(..., description="Earliest scored_at")
    last_scored_at: datetime = Field(..., description="Latest scored_at")
    freshness_hours: float = Field(..., description="Hours since last_scored_at")


class RollupResponse(BaseModel):
    generated_at: datetime = Field(..., description="Timestamp of response generation")
    days: int = Field(..., description="Look‑back window in days")
    versions: List[VersionInfo] = Field(..., description="Aggregated version data")


@router.get(
    "/model-versions",
    response_model=RollupResponse,
    summary="Roll‑up of scoring model / adapter / decision‑rule versions",
)
def get_model_versions(
    days: int = Query(30, ge=1, le=365, description="Look‑back window in days"),
    session: Session = Depends(get_session),
) -> RollupResponse:
    """Aggregate axis scores by model / decision‑rule / adapter."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    model_col = func.coalesce(McpLlmAxisScore.model_version, "unknown")
    dr_col = func.coalesce(McpLlmAxisScore.decision_rule_version, "unknown")
    adapter_col = func.coalesce(McpLlmAxisScore.adapter_sha256, "unknown")

    overall_risk_case = case(
        (McpLlmAxisScore.axis_name == "overall_risk", 1), else_=0
    )

    rows = (
        session.query(
            model_col.label("model_version"),
            dr_col.label("decision_rule_version"),
            adapter_col.label("adapter_sha256"),
            func.count().label("score_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "distinct_server_count"
            ),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label(
                "distinct_axis_count"
            ),
            func.sum(overall_risk_case).label("overall_risk_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            model_col,
            dr_col,
            adapter_col,
        )
        .all()
    )

    now = datetime.utcnow()
    versions: List[VersionInfo] = []
    for r in rows:
        freshness = (now - r.last_scored_at).total_seconds() / 3600.0
        versions.append(
            VersionInfo(
                model_version=r.model_version,
                decision_rule_version=r.decision_rule_version,
                adapter_sha256=r.adapter_sha256,
                score_count=r.score_count,
                distinct_server_count=r.distinct_server_count,
                distinct_axis_count=r.distinct_axis_count,
                overall_risk_count=r.overall_risk_count,
                first_scored_at=r.first_scored_at,
                last_scored_at=r.last_scored_at,
                freshness_hours=round(freshness, 2),
            )
        )

    return RollupResponse(generated_at=now, days=days, versions=versions)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # Build a minimal FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Override the session dependency
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed test data (3 rows, 2 distinct model_version values)
    now = datetime.utcnow()
    test_rows = [
        McpLlmAxisScore(
            id=1,
            server_id=1,
            axis_name="overall_risk",
            label="low",
            label_index=0,
            probs="[]",
            p_top=0.1,
            p_critical=0.0,
            p_danger=0.0,
            escalated=False,
            escalated_to=None,
            decision_rule_version="dr1",
            model_version="sft-v1",
            adapter_sha256="a1",
            scored_at=now - timedelta(hours=1),
        ),
        McpLlmAxisScore(
            id=2,
            server_id=2,
            axis_name="overall_risk",
            label="medium",
            label_index=1,
            probs="[]",
            p_top=0.2,
            p_critical=0.0,
            p_danger=0.0,
            escalated=False,
            escalated_to=None,
            decision_rule_version="dr2",
            model_version="sft-v2",
            adapter_sha256="a2",
            scored_at=now - timedelta(hours=2),
        ),
        McpLlmAxisScore(
            id=3,
            server_id=2,
            axis_name="some_axis",
            label="high",
            label_index=2,
            probs="[]",
            p_top=0.3,
            p_critical=0.0,
            p_danger=0.0,
            escalated=False,
            escalated_to=None,
            decision_rule_version="dr2",
            model_version="sft-v2",
            adapter_sha256="a2",
            scored_at=now - timedelta(hours=3),
        ),
    ]

    with SessionLocal() as db:
        db.add_all(test_rows)
        db.commit()

    # Run test client
    client = TestClient(app)
    response = client.get("/api/scoring/model-versions?days=30")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert isinstance(data, dict), "Response is not a dict"
    versions = data.get("versions", [])
    assert len(versions) == 2, f"Expected 2 versions, got {len(versions)}"
    total_scores = sum(v["score_count"] for v in versions)
    assert total_scores == 3, f"Expected total score_count 3, got {total_scores}"
    # Find sft-v2 entry
    v2 = next((v for v in versions if v["model_version"] == "sft-v2"), None)
    assert v2 is not None, "sft-v2 version missing"
    assert (
        v2["distinct_axis_count"] == 2
    ), f"Expected distinct_axis_count 2 for sft-v2, got {v2['distinct_axis_count']}"
    print("PASS")
    sys.exit(0)