# deps: fastapi, pydantic, sqlalchemy
"""Server Axis Values API.
Provides GET /servers/{server_id}/axis-values returning the raw axis records for a
server. Mirrors the structure of other API modules such as
`scoring_summary_api.py` but returns the full set of columns without any risk
tier derivation.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_values"])

class AxisValue(BaseModel):
    axis_name: str
    label: str | None = None
    label_index: int | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None
    scored_at: datetime | None = None
    model_version: str | None = None

@router.get("/servers/{server_id}/axis-values", response_model=List[AxisValue])
def get_axis_values(server_id: str, db: Session = Depends(get_session)) -> List[AxisValue]:
    """Return the 7 risk‑axis records for *server_id*.
    If no rows exist a 404 is raised.
    """
    rows = db.execute(
        select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No axis values for server_id {server_id!r}")
    # Preserve the canonical axis order
    AXES = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )
    # Build a map then output in order, filling missing axes with defaults
    axis_map = {r.axis_name: r for r in rows}
    result: List[AxisValue] = []
    for a in AXES:
        r = axis_map.get(a)
        if r:
            result.append(
                AxisValue(
                    axis_name=r.axis_name,
                    label=r.label,
                    label_index=r.label_index,
                    p_top=r.p_top,
                    p_critical=r.p_critical,
                    p_danger=r.p_danger,
                    escalated=r.escalated,
                    scored_at=r.scored_at,
                    model_version=r.model_version,
                )
            )
        else:
            # If an axis is missing, still include a placeholder with just the name
            result.append(AxisValue(axis_name=a))
    return result

if __name__ == "__main__":  # CI‑safe self‑test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In‑memory SQLite for testing
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed data – one row per axis using only required constructor kwargs
    s = SessionLocal()
    for i, (ax, lbl) in enumerate(
        [
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"),
            ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE"),
        ],
        start=1,
    ):
        s.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
            )
        )
    s.commit()
    s.close()

    # Dependency override
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
    resp = client.get("/api/servers/srv1/axis-values")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) == 7, f"Expected 7 axis items, got {len(data)}"
    # Spot‑check a field
    assert data[0]["axis_name"] == "overall_risk"
    print("PASS")
