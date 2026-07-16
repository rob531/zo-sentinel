"""axis_top_labels_api.py -- Per-server axis top-label endpoint.

Returns the top-scored label (p_top) and probability metadata for each of the
7 risk axes from mcp_llm_axis_scores, plus the overall top label.

Mounted automatically by app.main via _OPTIONAL_ROUTERS.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["axis"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisTopLabel(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[str] = None


class AxisTopLabelsResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    axes: Dict[str, AxisTopLabel]
    overall_top_label: Optional[str] = None
    trusted: bool = False
    trust_basis: Optional[str] = None


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/axis-top-labels", response_model=AxisTopLabelsResponse)
def get_axis_top_labels(server_id: str, db: Session = Depends(get_session)) -> AxisTopLabelsResponse:
    """Per-server axis top-labels = the 7 axis rows for the latest model_version with
    p_top / p_critical / p_danger probabilities and scored_at timestamp."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    axes: Dict[str, AxisTopLabel] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        axes[r.axis_name] = AxisTopLabel(
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            scored_at=r.scored_at.isoformat() if r.scored_at else None,
        )
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)
    overall_top = labels.get("overall_risk")

    return AxisTopLabelsResponse(
        server_id=server_id,
        name=name,
        url=url,
        model_version=mv,
        axes=axes,
        overall_top_label=gate.get("published_overall_risk") or overall_top,
        trusted=bool(gate.get("trusted")),
        trust_basis=gate.get("trust_basis"),
    )


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="test-server", name="Test MCP",
                            url="https://github.com/test/mcp"))
    # Seed axis scores with p_top values (required for the acceptance test)
    axis_data = [
        ("overall_risk", "HIGH", 0.72),
        ("auth_strength", "STRONG", 0.65),
        ("capability_breadth", "BROAD", 0.58),
        ("data_sensitivity", "CRITICAL", 0.81),
        ("network_egress", "EXTERNAL", 0.45),
        ("maintainer_trust", "ESTABLISHED", 0.70),
        ("exploit_surface", "MODERATE", 0.55),
    ]
    for _i, (ax, lbl, ptop) in enumerate(axis_data, start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="test-server", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559", p_top=ptop))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    r = c.get("/api/servers/test-server/axis-top-labels")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "test-server", j
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    for ax in AXES:
        assert ax in j["axes"], f"Missing axis: {ax}"
        assert j["axes"][ax]["label"] is not None, f"Missing label for {ax}"
        assert j["axes"][ax]["p_top"] is not None, f"Missing p_top for {ax}"

    # Verify overall_top_label is present
    assert j["overall_top_label"] is not None, j

    # Verify 404 for unknown server
    r404 = c.get("/api/servers/nonexistent-server/axis-top-labels")
    assert r404.status_code == 404, r404.text

    print("PASS")
