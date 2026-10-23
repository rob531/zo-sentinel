"""Logic for the ``app_verdict_breakdown`` service.

Provides a FastAPI router exposing ``GET /api/verdicts/{server_id}/breakdown``.
The endpoint returns a structured breakdown of the seven LLM risk axes for a
given server together with server metadata.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application data layer -------------------------------------------------
from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpServerRegistry,
)

# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------
class AxisBreakdown(BaseModel):
    axis_name: str
    label: str
    probability: float


class VerdictBreakdown(BaseModel):
    server_id: int
    name: str
    verdict: str
    risk_tier: str
    confidence: float
    criteria_version: str | None = None
    axes: List[AxisBreakdown]


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------
_RISK_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def _fetch_server(db: Session, server_id: int) -> McpServerRegistry:
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _fetch_axis_scores(db: Session, server_id: int) -> List[McpLlmAxisScore]:
    return (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )


def get_verdict_breakdown(server_id: int, db: Session) -> VerdictBreakdown:
    """Return a full verdict breakdown for ``server_id``."""
    server = _fetch_server(db, server_id)
    axis_scores = _fetch_axis_scores(db, server_id)

    # Map axis_name -> score for quick lookup
    score_map = {s.axis_name: s for s in axis_scores}

    axes: List[AxisBreakdown] = []
    for axis in _RISK_AXES:
        score = score_map.get(axis)
        if not score:
            # If a particular axis is missing we still include a placeholder
            axes.append(
                AxisBreakdown(
                    axis_name=axis,
                    label="unknown",
                    probability=0.0,
                )
            )
            continue

        # ``p_top`` holds the probability for the top label; ``label`` holds the label.
        probability = float(score.p_top) if score.p_top is not None else 0.0
        axes.append(
            AxisBreakdown(
                axis_name=axis,
                label=score.label or "unknown",
                probability=probability,
            )
        )

    return VerdictBreakdown(
        server_id=server.server_id,
        name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        confidence=float(server.confidence) if server.confidence is not None else 0.0,
        criteria_version=(
            axis_scores[0].decision_rule_version
            if axis_scores
            else None
        ),
        axes=axes,
    )


# ---------------------------------------------------------------------------
# Router definition
# ---------------------------------------------------------------------------
@router.get(
    "/verdicts/{server_id}/breakdown",
    response_model=VerdictBreakdown,
    name="get_verdict_breakdown",
)
def api_get_verdict_breakdown(
    server_id: int,
    db: Session = Depends(get_session),
) -> VerdictBreakdown:
    """FastAPI endpoint – thin wrapper around :func:`get_verdict_breakdown`. """
    return get_verdict_breakdown(server_id, db)


# ---------------------------------------------------------------------------
# Self‑test (executed when the module is run directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Build an in‑memory SQLite DB that mimics the real schema
    # -----------------------------------------------------------------------
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables using the metadata shared by the ORM models
    McpServerRegistry.metadata.create_all(bind=engine)

    # -----------------------------------------------------------------------
    # Dependency override for FastAPI
    # -----------------------------------------------------------------------
    def _override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Seed the test DB
    # -----------------------------------------------------------------------
    with SessionLocal() as db:
        # Server entry
        server = McpServerRegistry(
            server_id=1,
            name="test-server",
            verdict="allow",
            risk_tier="low",
            confidence=0.92,
            registry_source="unit-test",
        )
        db.add(server)

        # Axis scores – one entry per required axis
        for idx, axis in enumerate(_RISK_AXES, start=1):
            score = McpLlmAxisScore(
                id=idx,
                server_id=1,
                axis_name=axis,
                label="low",
                label_index=0,
                decision_rule_version="v1.0",
                p_top=0.85,
                p_critical=0.0,
                p_danger=0.0,
                probs="{}",
                adapter_sha256="deadbeef" * 8,
                model_version="test-model",
                scored_at=datetime.utcnow(),
            )
            db.add(score)

        db.commit()

    # -----------------------------------------------------------------------
    # Assemble FastAPI app for the test
    # -----------------------------------------------------------------------
    from fastapi import FastAPI

    app = FastAPI()
    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(router)

    client = TestClient(app)

    # -----------------------------------------------------------------------
    # Execute the request and validate the response
    # -----------------------------------------------------------------------
    resp = client.get("/api/verdicts/1/breakdown")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    # Basic sanity checks
    assert payload["server_id"] == 1
    assert payload["name"] == "test-server"
    assert len(payload["axes"]) == 7, "Expected 7 axes in the breakdown"

    axis_names = {axis["axis_name"] for axis in payload["axes"]}
    assert axis_names == set(_RISK_AXES), "Axis labels do not match expected enum"

    print("PASS")