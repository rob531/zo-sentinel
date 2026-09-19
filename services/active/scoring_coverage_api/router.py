# deps: fastapi, sqlalchemy, pydantic
"""Scoring coverage API -- reports what fraction of registry servers have LLM scores.

GET /api/scoring/coverage
  Returns coverage statistics (total, llm-scored, legacy-scored, percentage).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry / mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on path for app imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_coverage_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class CoverageResponse(BaseModel):
    total_servers: int
    llm_scored_servers: int
    legacy_scored_servers: int
    coverage_pct: float
    scored_at: datetime


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/scoring/coverage", response_model=CoverageResponse)
def scoring_coverage(session: Session = Depends(get_session)) -> CoverageResponse:
    """
    Return scoring coverage: what fraction of the registry has an LLM overall_risk score.
    """
    total_rows = session.query(McpServerRegistry).count()

    llm_scored = (
        session.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .distinct()
        .count()
    )

    legacy_scored = max(total_rows - llm_scored, 0)
    coverage_pct = round((llm_scored / total_rows) * 100, 2) if total_rows else 0.0

    return CoverageResponse(
        total_servers=total_rows,
        llm_scored_servers=llm_scored,
        legacy_scored_servers=legacy_scored,
        coverage_pct=coverage_pct,
        scored_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    sys.path.insert(0, "/home/workspace/zo_sentinel")
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Seed: 100 servers, 60 with overall_risk scores
    with TestSessionLocal() as db:
        for i in range(100):
            db.add(McpServerRegistry(server_id=f"srv-{i:04d}", name=f"Server {i}"))
        for i in range(60):
            db.add(
                McpLlmAxisScore(
                    id=i + 1,
                    server_id=f"srv-{i:04d}",
                    axis_name="overall_risk",
                    model_version="v1",
                    label="medium",
                    label_index=2,
                    p_critical=0.1,
                    p_danger=0.3,
                    p_top=0.6,
                    probs={},
                    scored_at=datetime.now(timezone.utc),
                )
            )
        db.commit()

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)
    resp = client.get("/api/scoring/coverage")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["total_servers"] == 100, f"total_servers: expected 100, got {data['total_servers']}"
    assert data["llm_scored_servers"] == 60, f"llm_scored_servers: expected 60, got {data['llm_scored_servers']}"
    assert data["legacy_scored_servers"] == 40, f"legacy_scored_servers: expected 40, got {data['legacy_scored_servers']}"
    assert 59 <= data["coverage_pct"] <= 61, f"coverage_pct: expected ~60, got {data['coverage_pct']}"
    assert "scored_at" in data, "Missing scored_at"

    print("PASS")
    sys.exit(0)
