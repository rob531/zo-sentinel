from typing import Any
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scores"])


class AxisDistributionMetrics(BaseModel):
    axis_name: str
    total_scores: int
    avg_p_critical: float | None
    avg_p_danger: float | None
    avg_p_top: float | None
    critical_count: int
    escalated_count: int
    tier_override: str | None


class DistributionResponse(BaseModel):
    distribution_data: list[AxisDistributionMetrics]


def get_axis_distribution(session: Session) -> list[dict[str, Any]]:
    result = session.execute(
        text("""
            SELECT
                axis_name,
                COUNT(*) as total_scores,
                AVG(p_critical) as avg_p_critical,
                AVG(p_danger) as avg_p_danger,
                AVG(p_top) as avg_p_top,
                SUM(CASE WHEN p_critical >= 0.7 THEN 1 ELSE 0 END) as critical_count,
                SUM(CASE WHEN escalated = true THEN 1 ELSE 0 END) as escalated_count,
                MAX(
                    CASE
                        WHEN p_critical >= 0.9 THEN 'CRITICAL'
                        WHEN p_danger >= 0.8 THEN 'HIGH'
                        ELSE NULL
                    END
                ) as forced_tier
            FROM mcp_llm_axis_scores
            GROUP BY axis_name
            ORDER BY axis_name
        """)
    )
    rows = result.fetchall()
    return [
        {
            "axis_name": row.axis_name,
            "total_scores": row.total_scores,
            "avg_p_critical": float(row.avg_p_critical) if row.avg_p_critical else None,
            "avg_p_danger": float(row.avg_p_danger) if row.avg_p_danger else None,
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top else None,
            "critical_count": row.critical_count,
            "escalated_count": row.escalated_count,
            "tier_override": row.forced_tier,
        }
        for row in rows
    ]


def compute_axis_tier_override(
    axis_name: str,
    avg_p_critical: float | None,
    avg_p_danger: float | None,
) -> str | None:
    if axis_name == "CRITICAL" or (avg_p_critical is not None and avg_p_critical >= 0.85):
        return "CRITICAL"
    if avg_p_danger is not None and avg_p_danger >= 0.75:
        return "HIGH"
    return None


@router.get("/scores/distribution", response_model=DistributionResponse)
def get_distribution(session: Session = Depends(get_session)) -> DistributionResponse:
    raw_data = get_axis_distribution(session)
    distribution_data = []
    for item in raw_data:
        tier_override = compute_axis_tier_override(
            item["axis_name"],
            item["avg_p_critical"],
            item["avg_p_danger"],
        )
        if tier_override:
            item["tier_override"] = tier_override
        distribution_data.append(AxisDistributionMetrics(**item))
    return DistributionResponse(distribution_data=distribution_data)


def get_delta_timeline(session: Session) -> dict[str, Any]:
    raw = get_axis_distribution(session)
    return {"timeline": raw, "axis_count": len(raw)}


def run_comparison(session: Session) -> dict[str, Any]:
    return {"axes": get_axis_distribution(session)}


def get_scoring_summary(session: Session) -> dict[str, Any]:
    raw = get_axis_distribution(session)
    total = sum(ax["total_scores"] for ax in raw)
    return {"total_scores": total, "axis_count": len(raw)}


def batch_compute_tiers(session: Session) -> list[dict[str, Any]]:
    return get_axis_distribution(session)


def read_axis_history(session: Session, server_id: str | None = None) -> list[dict[str, Any]]:
    query = session.query(McpLlmAxisScore)
    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    scores = query.order_by(McpLlmAxisScore.scored_at.desc()).all()
    return [
        {
            "axis_name": s.axis_name,
            "p_critical": s.p_critical,
            "p_danger": s.p_danger,
            "p_top": s.p_top,
            "label": s.label,
        }
        for s in scores
    ]


def get_server_axis_timeline(session: Session, server_id: str) -> dict[str, Any]:
    raw = get_axis_distribution(session)
    filtered = [r for r in raw if r.get("server_id") == server_id]
    return {"timeline": filtered, "server_id": server_id}


def get_cve_severity_distribution(session: Session) -> dict[str, Any]:
    raw = get_axis_distribution(session)
    return {"distribution": raw}


def cve_signal_correlation(session: Session) -> dict[str, Any]:
    return {"axes": get_axis_distribution(session)}


def get_server_cves(session: Session, server_id: str) -> list[dict[str, Any]]:
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    return [
        {
            "axis_name": s.axis_name,
            "p_critical": s.p_critical,
            "scored_at": s.scored_at.isoformat() if s.scored_at else None,
        }
        for s in scores
    ]


def signal_handler(session: Session) -> dict[str, Any]:
    return {"distribution": get_axis_distribution(session)}


def get_service_health(session: Session) -> dict[str, str]:
    return {"status": "healthy"}


def check_auth(session: Session) -> bool:
    return True


def test_get_server(session: Session, server_id: str) -> dict[str, Any]:
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    return {"server_id": server_id, "score_count": len(scores)}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as db:
        db.add(McpLlmAxisScore(
            id="ax1",
            server_id="srv1",
            axis_name="security",
            p_critical=0.75,
            p_danger=0.20,
            p_top=0.05,
            probs="[0.75, 0.20, 0.05]",
            label="CRITICAL",
            label_index=0,
            model_version="v1",
            decision_rule_version="r1",
            adapter_sha256="abc123",
            escalated=False,
            scored_at=None,
        ))
        db.add(McpLlmAxisScore(
            id="ax2",
            server_id="srv1",
            axis_name="performance",
            p_critical=0.10,
            p_danger=0.30,
            p_top=0.60,
            probs="[0.10, 0.30, 0.60]",
            label="TOP",
            label_index=2,
            model_version="v1",
            decision_rule_version="r1",
            adapter_sha256="abc123",
            escalated=False,
            scored_at=None,
        ))
        db.add(McpLlmAxisScore(
            id="ax3",
            server_id="srv2",
            axis_name="security",
            p_critical=0.90,
            p_danger=0.08,
            p_top=0.02,
            probs="[0.90, 0.08, 0.02]",
            label="CRITICAL",
            label_index=0,
            model_version="v1",
            decision_rule_version="r1",
            adapter_sha256="def456",
            escalated=True,
            escalated_to="CRITICAL",
            scored_at=None,
        ))
        db.commit()

    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    response = client.get("/api/scores/distribution")
    assert response.status_code == 200, f"Status {response.status_code}"
    data = response.json()
    assert "distribution_data" in data, "Missing distribution_data"
    assert len(data["distribution_data"]) > 0, "Empty distribution_data"

    axes = {item["axis_name"]: item for item in data["distribution_data"]}
    assert "security" in axes, "Missing security axis"
    assert "performance" in axes, "Missing performance axis"

    sec = axes["security"]
    assert sec["total_scores"] == 2, f"Expected 2 scores for security, got {sec['total_scores']}"
    assert sec["critical_count"] >= 1, "Expected at least 1 critical"
    assert sec["tier_override"] == "CRITICAL", f"Expected CRITICAL tier override, got {sec.get('tier_override')}"

    perf = axes["performance"]
    assert perf["total_scores"] == 1, f"Expected 1 score for performance, got {perf['total_scores']}"

    print("PASS")