# deps: fastapi, pydantic, sqlalchemy
"""Server Axis Summary API.

Provides a FastAPI router exposing GET /servers/{server_id}/axis-summary that returns
a concise summary of all seven risk axes for a given server, including each axis label,
top probability (p_top), and critical probability (p_critical).

Interface: def get_axis_summary(server_id: str) -> dict
  -> {"server_id": str, "axes": {axis_name: {"label": str, "p_top": float, "p_critical": float}}}

Pure read-only; uses the app.db SQLAlchemy session.
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_summary"])


class AxisSummary(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None


class AxisSummaryResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisSummary]


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/axis-summary", response_model=AxisSummaryResponse)
def get_axis_summary(server_id: str, db: Session = Depends(get_session)) -> AxisSummaryResponse:
    """Return a concise summary of all seven risk axes for *server_id*.

    Axes: overall_risk, auth_strength, capability_breadth, data_sensitivity,
          network_egress, maintainer_trust, exploit_surface.
    """
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    AXES = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )

    axis_map = {r.axis_name: r for r in rows}
    axes: Dict[str, AxisSummary] = {}
    for ax in AXES:
        r = axis_map.get(ax)
        if r:
            axes[ax] = AxisSummary(label=r.label, p_top=r.p_top, p_critical=r.p_critical)
        else:
            axes[ax] = AxisSummary()

    return AxisSummaryResponse(server_id=server_id, axes=axes)


if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In-memory SQLite for test isolation
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed data – one row per axis using only required constructor kwargs
    s = SessionLocal()
    s.add(
        McpLlmAxisScore(
            id=1, server_id="srv1", axis_name="overall_risk",
            label="HIGH", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=2, server_id="srv1", axis_name="auth_strength",
            label="STRONG", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=3, server_id="srv1", axis_name="capability_breadth",
            label="BROAD", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=4, server_id="srv1", axis_name="data_sensitivity",
            label="CRITICAL", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=5, server_id="srv1", axis_name="network_egress",
            label="EXTERNAL", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=6, server_id="srv1", axis_name="maintainer_trust",
            label="ESTABLISHED", model_version="v3.0_40974559",
        )
    )
    s.add(
        McpLlmAxisScore(
            id=7, server_id="srv1", axis_name="exploit_surface",
            label="MODERATE", model_version="v3.0_40974559",
        )
    )
    s.commit()
    s.close()

    # Monkeypatch requests.post to prevent real HTTP during self-test
    import unittest.mock as umock
    with umock.patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200

        def _override_session():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = _override_session

        client = TestClient(app)

        # Happy path – server with all 7 axes
        resp = client.get("/api/servers/srv1/axis-summary")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["server_id"] == "srv1"
        assert "axes" in data
        axes = data["axes"]
        assert len(axes) == 7, f"Expected 7 axes, got {len(axes)}"
        for key in ("overall_risk", "auth_strength", "capability_breadth",
                    "data_sensitivity", "network_egress", "maintainer_trust",
                    "exploit_surface"):
            assert key in axes, f"Missing axis {key}"
            assert "label" in axes[key], f"Missing label in axis {key}"
            assert "p_top" in axes[key], f"Missing p_top in axis {key}"
            assert "p_critical" in axes[key], f"Missing p_critical in axis {key}"
        assert axes["overall_risk"]["label"] == "HIGH"

        # Edge case – server not found
        resp2 = client.get("/api/servers/nope/axis-summary")
        assert resp2.status_code == 404

    print("PASS")
