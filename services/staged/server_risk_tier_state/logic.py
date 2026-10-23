# services/staged/server_risk_tier_state/logic.py
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


class TierInfo(BaseModel):
    tier: Optional[str] = Field(..., description="Risk tier name")
    count: int = Field(..., description="Number of servers in this tier")
    pct: float = Field(..., description="Percentage of total servers")
    latest_assessment: Optional[datetime] = Field(
        None, description="Most recent last_assessed timestamp in this tier"
    )


class SummaryInfo(BaseModel):
    total: int = Field(..., description="Total number of servers")
    with_scores: int = Field(..., description="Servers that have an overall_risk score")
    coverage_pct: float = Field(..., description="Percentage of servers with scores")
    computed_at: datetime = Field(..., description="Timestamp of computation")


class RiskStateResponse(BaseModel):
    tiers: List[TierInfo]
    summary: SummaryInfo


@router.get("/risk/state", response_model=RiskStateResponse)
def get_risk_state(db: Session = Depends(get_session)):
    # total servers
    total_servers: int = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # servers that have an overall_risk axis score
    scores_subq = (
        db.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )
    with_scores: int = (
        db.query(func.count(McpServerRegistry.server_id))
        .join(scores_subq, McpServerRegistry.server_id == scores_subq.c.server_id)
        .scalar()
        or 0
    )

    # tier aggregation
    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier.label("tier"),
            func.count(McpServerRegistry.server_id).label("count"),
            func.max(McpServerRegistry.last_assessed).label("latest_assessment"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers: List[TierInfo] = []
    for row in tier_rows:
        pct = (row.count / total_servers * 100) if total_servers else 0.0
        tiers.append(
            TierInfo(
                tier=row.tier,
                count=row.count,
                pct=round(pct, 2),
                latest_assessment=row.latest_assessment,
            )
        )

    coverage_pct = (with_scores / total_servers * 100) if total_servers else 0.0

    summary = SummaryInfo(
        total=total_servers,
        with_scores=with_scores,
        coverage_pct=round(coverage_pct, 2),
        computed_at=datetime.utcnow(),
    )

    return RiskStateResponse(tiers=tiers, summary=summary)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite setup
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="s1",
                risk_tier="low",
                last_assessed=datetime(2023, 1, 10, 12, 0, 0),
                last_scanned=datetime(2023, 1, 9, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="s2",
                risk_tier="medium",
                last_assessed=datetime(2023, 2, 15, 12, 0, 0),
                last_scanned=datetime(2023, 2, 14, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="s3",
                risk_tier="high",
                last_assessed=datetime(2023, 3, 20, 12, 0, 0),
                last_scanned=datetime(2023, 3, 19, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="s4",
                risk_tier="critical",
                last_assessed=datetime(2023, 4, 25, 12, 0, 0),
                last_scanned=datetime(2023, 4, 24, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="s5",
                risk_tier="low",
                last_assessed=datetime(2023, 5, 5, 12, 0, 0),
                last_scanned=datetime(2023, 5, 4, 12, 0, 0),
            ),
        ]
        db.add_all(servers)

        scores = [
            McpLlmAxisScore(
                server_id="s1",
                axis_name="overall_risk",
                scored_at=datetime(2023, 1, 11, 12, 0, 0),
                p_critical=0.1,
                p_danger=0.2,
                p_top=0.7,
                probs="{}",
                label="low",
                label_index=0,
                model_version="v1",
                decision_rule_version="dr1",
                escalated=False,
                escalated_to=None,
                adapter_sha256="a1",
                id=1,
            ),
            McpLlmAxisScore(
                server_id="s2",
                axis_name="overall_risk",
                scored_at=datetime(2023, 2, 16, 12, 0, 0),
                p_critical=0.2,
                p_danger=0.3,
                p_top=0.5,
                probs="{}",
                label="medium",
                label_index=1,
                model_version="v1",
                decision_rule_version="dr1",
                escalated=False,
                escalated_to=None,
                adapter_sha256="a2",
                id=2,
            ),
            McpLlmAxisScore(
                server_id="s4",
                axis_name="overall_risk",
                scored_at=datetime(2023, 4, 26, 12, 0, 0),
                p_critical=0.6,
                p_danger=0.3,
                p_top=0.1,
                probs="{}",
                label="critical",
                label_index=3,
                model_version="v1",
                decision_rule_version="dr1",
                escalated=True,
                escalated_to="teamX",
                adapter_sha256="a4",
                id=4,
            ),
        ]
        db.add_all(scores)
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/risk/state")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict)
    assert "tiers" in data and "summary" in data
    assert len(data["tiers"]) >= 3, "Expected at least three tiers"
    summary = data["summary"]
    total = summary["total"]
    with_scores = summary["with_scores"]
    coverage = summary["coverage_pct"]
    expected_coverage = round((with_scores / total) * 100, 2) if total else 0.0
    assert coverage == expected_coverage, "Coverage percentage mismatch"
    print("PASS")