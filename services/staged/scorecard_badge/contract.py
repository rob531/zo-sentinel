# services/staged/scorecard_badge/contract.py
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")


class ScorecardBadgeResponse(BaseModel):
    server_id: int
    badge: str
    composite_score: float
    top_risk_axis: Optional[str]
    axis_count: int
    scored_at: datetime


def _compute_badge(
    axis_scores: List[McpLlmAxisScore],
) -> tuple[str, float, Optional[str], int]:
    """Return badge, composite_score, top_risk_axis, axis_count."""
    if not axis_scores:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No scores")

    scores_by_axis = {row.axis: row.score for row in axis_scores}
    axis_count = len(scores_by_axis)

    # INSUFFICIENT overrides everything
    if axis_count < 4:
        return "INSUFFICIENT", 0.0, None, axis_count

    # Composite score: average of axis scores
    composite_score = sum(scores_by_axis.values()) / axis_count

    # Determine top‑risk axis (lowest score)
    top_risk_axis = min(scores_by_axis, key=scores_by_axis.get)

    # Gather p_critical values (assume same for all rows)
    p_critical_vals = [row.p_critical for row in axis_scores if hasattr(row, "p_critical")]
    p_critical = max(p_critical_vals) if p_critical_vals else 0.0

    # Badge logic
    if any(score < 30 for score in scores_by_axis.values()) or p_critical > 0.6:
        badge = "HIGH_RISK"
    elif any(score < 50 for score in scores_by_axis.values()) or p_critical > 0.3:
        badge = "CAUTION"
    elif all(score >= 70 for score in scores_by_axis.values()):
        badge = "TRUSTED"
    else:
        badge = "CAUTION"  # fallback

    return badge, composite_score, top_risk_axis, axis_count


@router.get(
    "/servers/{server_id}/scorecard",
    response_model=ScorecardBadgeResponse,
    status_code=status.HTTP_200_OK,
)
def get_scorecard(
    server_id: int, session: Session = Depends(get_session)
) -> ScorecardBadgeResponse:
    # Verify server exists
    server = session.get(McpServerRegistry, server_id)
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"
        )

    # Retrieve axis scores for the server
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    badge, composite_score, top_risk_axis, axis_count = _compute_badge(rows)

    return ScorecardBadgeResponse(
        server_id=server_id,
        badge=badge,
        composite_score=round(composite_score, 2),
        top_risk_axis=top_risk_axis,
        axis_count=axis_count,
        scored_at=datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.scorecard_badge.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite DB and bind the app's Base metadata
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    # Session factory for the test DB
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSession() as sess:
        # Server 1 – TRUSTED (all axes >=70, low p_critical)
        sess.add(McpServerRegistry(server_id=1, name="Trusted Server"))
        for axis in ["confidentiality", "integrity", "availability", "privacy", "auth", "audit", "risk"]:
            sess.add(
                McpLlmAxisScore(
                    server_id=1,
                    axis=axis,
                    score=80.0,
                    p_critical=0.1,
                )
            )

        # Server 2 – CAUTION (one axis <50, moderate p_critical)
        sess.add(McpServerRegistry(server_id=2, name="Caution Server"))
        for axis in ["confidentiality", "integrity", "availability", "privacy", "auth", "audit", "risk"]:
            score = 45.0 if axis == "integrity" else 75.0
            sess.add(
                McpLlmAxisScore(
                    server_id=2,
                    axis=axis,
                    score=score,
                    p_critical=0.4,
                )
            )

        # Server 3 – INSUFFICIENT (only 3 axes)
        sess.add(McpServerRegistry(server_id=3, name="Insufficient Server"))
        for axis in ["confidentiality", "integrity", "availability"]:
            sess.add(
                McpLlmAxisScore(
                    server_id=3,
                    axis=axis,
                    score=60.0,
                    p_critical=0.0,
                )
            )
        sess.commit()

    client = TestClient(app)

    expectations = {
        1: "TRUSTED",
        2: "CAUTION",
        3: "INSUFFICIENT",
    }

    for sid, expected_badge in expectations.items():
        resp = client.get(f"/api/servers/{sid}/scorecard")
        if resp.status_code != 200:
            print(f"FAIL: server {sid} returned {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        if data.get("badge") != expected_badge:
            print(
                f"FAIL: server {sid} badge {data.get('badge')} != expected {expected_badge}"
            )
            sys.exit(1)

    print("PASS")
    sys.exit(0)