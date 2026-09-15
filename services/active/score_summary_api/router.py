# deps: fastapi, pydantic, sqlalchemy
"""Score Summary API — aggregate risk/tier statistics across scored MCP servers.

GET /api/scores/summary   Aggregate score summary (total servers, axis averages,
                          tier distribution, last scored timestamp).

Auth: public.
Data: app-db via get_session + McpLlmAxisScore + McpServerRegistry ORM.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on path so `from app.db` resolves correctly
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_summary"])


# --- Pydantic request/response shapes -------------------------------------

class ScoreSummaryResponse(BaseModel):
    total_servers: int
    axis_averages: dict[str, float]
    tier_distribution: dict[str, int]
    last_scored_at: str | None


# --- Endpoint ---------------------------------------------------------------

@router.get("/scores/summary", response_model=ScoreSummaryResponse)
def get_score_summary(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    total_servers = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .scalar()
        or 0
    )

    axis_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )
    axis_averages: dict[str, float] = {
        row.axis_name: float(row.avg_p_top) for row in axis_rows
    }

    # Count distinct servers per tier — use a subquery so servers with multiple
    # axis scores are not double-counted.
    scored_sids = (
        db.query(func.distinct(McpLlmAxisScore.server_id).label("sid"))
        .subquery()
    )
    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(scored_sids.c.sid).label("cnt"),
        )
        .join(scored_sids, McpServerRegistry.server_id == scored_sids.c.sid)
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    tier_distribution: dict[str, int] = {row.risk_tier: row.cnt for row in tier_rows}

    last_scored_at = db.query(func.max(McpLlmAxisScore.scored_at)).scalar()
    last_scored_at_str: str | None = (
        last_scored_at.isoformat() if last_scored_at else None
    )

    return ScoreSummaryResponse(
        total_servers=total_servers,
        axis_averages=axis_averages,
        tier_distribution=tier_distribution,
        last_scored_at=last_scored_at_str,
    )


# --- Self-test -------------------------------------------------------------

if __name__ == "__main__":
    # Bypass any broken top-level imports in app/__init__.py
    _app_db_mod = __import__("app.db", fromlist=["get_session"])
    _get_session = _app_db_mod.get_session
    _app_models_mod = __import__("app.models", fromlist=["Base", "McpLlmAxisScore", "McpServerRegistry"])
    Base = _app_models_mod.Base
    McpLlmAxisScore = _app_models_mod.McpLlmAxisScore
    McpServerRegistry = _app_models_mod.McpServerRegistry

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_session] = override_get_session

    now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    model_ver = "test-v1"

    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="s1", name="Server1", risk_tier="high",
                url="http://s1.example.com", registry_source="test",
            ),
            McpServerRegistry(
                server_id="s2", name="Server2", risk_tier="high",
                url="http://s2.example.com", registry_source="test",
            ),
            McpServerRegistry(
                server_id="s3", name="Server3", risk_tier="high",
                url="http://s3.example.com", registry_source="test",
            ),
            McpServerRegistry(
                server_id="s4", name="Server4", risk_tier="low",
                url="http://s4.example.com", registry_source="test",
            ),
            McpServerRegistry(
                server_id="s5", name="Server5", risk_tier="low",
                url="http://s5.example.com", registry_source="test",
            ),
        ]
        db.add_all(servers)
        db.flush()

        scores = [
            # s1 has 2 axes
            McpLlmAxisScore(
                id=1,
                server_id="s1", axis_name="overall_risk", p_top=0.9,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="s1", axis_name="auth_strength", p_top=0.85,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
            # s2 has 1 axis
            McpLlmAxisScore(
                id=3,
                server_id="s2", axis_name="overall_risk", p_top=0.75,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
            # s3 has 1 axis
            McpLlmAxisScore(
                id=4,
                server_id="s3", axis_name="overall_risk", p_top=0.6,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
            # s4 has 1 axis
            McpLlmAxisScore(
                id=5,
                server_id="s4", axis_name="overall_risk", p_top=0.5,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
            # s5 has 1 axis
            McpLlmAxisScore(
                id=6,
                server_id="s5", axis_name="overall_risk", p_top=0.4,
                scored_at=now, model_version=model_ver,
                decision_rule_version="r1",
            ),
        ]
        db.add_all(scores)
        db.commit()

    client = TestClient(app)
    response = client.get("/api/scores/summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    assert "total_servers" in data
    assert data["total_servers"] == 5, f"Expected 5 distinct servers, got {data['total_servers']}"

    assert "axis_averages" in data
    assert data["axis_averages"], "axis_averages should be populated"
    assert "overall_risk" in data["axis_averages"]

    assert "tier_distribution" in data
    assert sum(data["tier_distribution"].values()) == 5, (
        f"tier_distribution should sum to 5, got {sum(data['tier_distribution'].values())}"
    )
    assert data["tier_distribution"].get("high") == 3, data["tier_distribution"]
    assert data["tier_distribution"].get("low") == 2, data["tier_distribution"]

    assert "last_scored_at" in data

    print("PASS")
    sys.exit(0)
