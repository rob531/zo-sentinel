# deps: fastapi, pydantic, sqlalchemy
"""Axis Critical Servers API

Provides a FastAPI router exposing GET /axis-critical-servers.
Returns the top N servers per risk axis ordered by the highest
`p_critical` score. Optional `axis_name` query parameter filters to a
single axis.

Mirrors the structure of ``verdict_breakdown_api.py`` but without
authentication or write side‑effects.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_critical"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

class AxisCriticalItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    axis_name: str
    label: Optional[str] = None
    p_critical: Optional[float] = None
    p_top: Optional[float] = None
    model_version: Optional[str] = None
    scored_at: Optional[datetime] = None

@router.get("/axis-critical-servers", response_model=List[AxisCriticalItem])
def get_axis_critical_servers(
    axis_name: Optional[str] = Query(None, description="Filter to a single axis"),
    limit: int = Query(10, ge=1, le=100, description="Maximum rows per axis"),
    db: Session = Depends(get_session),
) -> List[AxisCriticalItem]:
    """Return the top ``limit`` servers for each risk axis ordered by ``p_critical``.

    If ``axis_name`` is supplied it must be one of the known axes and the
    result is limited to that axis only.
    """
    axes_to_query = [axis_name] if axis_name else list(AXES)
    if axis_name and axis_name not in AXES:
        raise HTTPException(status_code=400, detail="Invalid axis_name")

    results: List[AxisCriticalItem] = []
    for ax in axes_to_query:
        stmt = (
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.axis_name == ax)
            .order_by(desc(McpLlmAxisScore.p_critical))
            .limit(limit)
        )
        rows = db.execute(stmt).scalars().all()
        for r in rows:
            reg = db.get(McpServerRegistry, r.server_id)
            results.append(
                AxisCriticalItem(
                    server_id=r.server_id,
                    name=reg.name if reg else None,
                    url=reg.url if reg else None,
                    axis_name=r.axis_name,
                    label=r.label,
                    p_critical=r.p_critical,
                    p_top=r.p_top,
                    model_version=r.model_version,
                    scored_at=r.scored_at,
                )
            )
    return results

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

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed minimal data – one server with a row for each axis
    db = SessionLocal()
    db.add(McpServerRegistry(server_id="srv1", name="Test Server", url="https://example.com"))
    for i, (ax, lbl, pcrit) in enumerate(
        [
            ("overall_risk", "HIGH", 0.99),
            ("auth_strength", "STRONG", 0.95),
            ("capability_breadth", "BROAD", 0.90),
            ("data_sensitivity", "CRITICAL", 0.85),
            ("network_egress", "EXTERNAL", 0.80),
            ("maintainer_trust", "ESTABLISHED", 0.75),
            ("exploit_surface", "MODERATE", 0.70),
        ],
        start=1,
    ):
        db.add(
            McpLlmAxisScore(
                id=i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
                p_critical=pcrit,
                p_top=pcrit,  # simple placeholder
            )
        )
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    # Full request – should contain 7 items (one per axis)
    resp = client.get("/api/axis-critical-servers")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) == 7, f"Expected 7 items, got {len(data)}"
    # Filtered request – only overall_risk
    resp2 = client.get("/api/axis-critical-servers", params={"axis_name": "overall_risk"})
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert len(data2) == 1 and data2[0]["axis_name"] == "overall_risk", "Filtered result incorrect"
    print("PASS")
