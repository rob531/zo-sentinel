"""axis_change_attribution_probe.py -- FastAPI probe: GET /probes/axes/attribution.

Detects axis label changes between model versions and attributes them to
signals/enrichments available in the mesh/pipeline store.
Reads app tables via SQLAlchemy; mesh signals via write_service :8772.
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

WRITE_SERVICE = "http://127.0.0.1:8772"
router = APIRouter(prefix="/api/probes", tags=["probes"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisChange(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    url: Optional[str] = None
    axis_name: str
    old_label: Optional[str] = None
    new_label: Optional[str] = None
    old_model_version: Optional[str] = None
    new_model_version: str
    attribution_sources: list[str] = []


class AttributionResponse(BaseModel):
    changes: list[AxisChange]
    total: int
    since_version: Optional[str] = None


def _fetch_signal_scores(server_id: str) -> list[dict]:
    """Fetch enrichment/signal scores for a server from the mesh store."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={
                "sql": (
                    "SELECT signal_name, score, enrichment_type, source "
                    "FROM mcp_signal_scores WHERE server_id = :sid"
                ),
                "params": [server_id],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("rows", []) or resp.json().get("data", []) or []
    except Exception:
        pass
    return []


def _fetch_mesh_memory(server_id: str) -> list[dict]:
    """Fetch pipeline memory/artifact links from the mesh store."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={
                "sql": (
                    "SELECT artifact_type, artifact_key, enrichment_name "
                    "FROM mesh_memory WHERE server_id = :sid"
                ),
                "params": [server_id],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("rows", []) or resp.json().get("data", []) or []
    except Exception:
        pass
    return []


def _attribution_sources(server_id: str, axis_name: str, new_label: Optional[str]) -> list[str]:
    """Gather attribution sources for an axis label change."""
    sources = []
    for row in _fetch_signal_scores(server_id):
        sig = row.get("signal_name") or row.get("source") or ""
        if sig:
            sources.append(sig)
    for row in _fetch_mesh_memory(server_id):
        art = row.get("enrichment_name") or row.get("artifact_type") or ""
        if art:
            sources.append(art)
    if new_label:
        sources.append(f"model_label:{new_label}")
    return list(dict.fromkeys(sources))  # dedupe preserve order


@router.get("/axes/attribution", response_model=AttributionResponse)
def get_axis_attribution(
    since_version: Optional[str] = None,
    axis: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_session),
) -> AttributionResponse:
    """Detect axis label changes between model versions and attribute each
    to signals/enrichments found in the mesh/pipeline store.

    - since_version: only show changes where new_model_version > this
    - axis: filter to one axis (e.g. 'overall_risk')
    - limit: max servers to analyse (default 100)
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1-500")
    if axis and axis not in AXES:
        raise HTTPException(status_code=400, detail=f"axis must be one of {AXES}")

    axes_to_check = (axis,) if axis else AXES

    # Find servers with scores in more than one model_version (i.e. a change occurred)
    subq = (
        select(McpLlmAxisScore.server_id, func.count(func.distinct(McpLlmAxisScore.model_version)).label("vc"))
        .where(McpLlmAxisScore.axis_name.in_(axes_to_check))
        .group_by(McpLlmAxisScore.server_id)
        .having(func.count(func.distinct(McpLlmAxisScore.model_version)) > 1)
        .subquery()
    )
    multi_version_servers = [
        r[0] for r in db.execute(select(subq.c.server_id).limit(limit)).all()
    ]

    if not multi_version_servers:
        return AttributionResponse(changes=[], total=0, since_version=since_version)

    changes: list[AxisChange] = []

    for server_id in multi_version_servers:
        # Get all model versions for this server, ordered
        ver_rows = db.execute(
            select(McpLlmAxisScore.model_version)
            .where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name.in_(axes_to_check),
            )
            .group_by(McpLlmAxisScore.model_version)
            .order_by(func.min(McpLlmAxisScore.scored_at).asc())
        ).scalars().all()

        versions = list(ver_rows)
        if len(versions) < 2:
            continue

        if since_version:
            since_idx = -1
            for i, v in enumerate(versions):
                if v == since_version:
                    since_idx = i
                    break
            if since_idx < 0:
                continue
            pairs = [(versions[i - 1], versions[i]) for i in range(since_idx + 1, len(versions))]
        else:
            # Compare adjacent versions
            pairs = [(versions[i - 1], versions[i]) for i in range(1, len(versions))]

        reg = db.get(McpServerRegistry, server_id)
        name = reg.name if reg else None
        url = reg.url if reg else None

        for old_ver, new_ver in pairs:
            for ax in axes_to_check:
                old_row = db.execute(
                    select(McpLlmAxisScore).where(
                        McpLlmAxisScore.server_id == server_id,
                        McpLlmAxisScore.axis_name == ax,
                        McpLlmAxisScore.model_version == old_ver,
                    )
                ).scalars().first()

                new_row = db.execute(
                    select(McpLlmAxisScore).where(
                        McpLlmAxisScore.server_id == server_id,
                        McpLlmAxisScore.axis_name == ax,
                        McpLlmAxisScore.model_version == new_ver,
                    )
                ).scalars().first()

                old_label = old_row.label if old_row else None
                new_label = new_row.label if new_row else None

                if old_label != new_label:
                    attribution = _attribution_sources(server_id, ax, new_label)
                    changes.append(AxisChange(
                        server_id=server_id,
                        server_name=name,
                        url=url,
                        axis_name=ax,
                        old_label=old_label,
                        new_label=new_label,
                        old_model_version=old_ver,
                        new_model_version=new_ver,
                        attribution_sources=attribution,
                    ))

    return AttributionResponse(
        changes=changes,
        total=len(changes),
        since_version=since_version,
    )


if __name__ == "__main__":
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

    # Seed: two servers, two model versions each
    # Server "srv1" has a label change: overall_risk MEDIUM -> HIGH
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Test MCP", url="https://example.com/test"))
    s.add(McpServerRegistry(server_id="srv2", name="Stable MCP", url="https://example.com/stable"))
    # v1 scores
    idx = 1
    for ax, lbl in (("overall_risk", "MEDIUM"), ("auth_strength", "WEAK"),
                    ("maintainer_trust", "NEW"), ("exploit_surface", "LOW")):
        s.add(McpLlmAxisScore(
            id=idx, server_id="srv1", axis_name=ax, label=lbl, model_version="v1.0"
        )); idx += 1
    for ax, lbl in (("overall_risk", "LOW"), ("auth_strength", "STRONG")):
        s.add(McpLlmAxisScore(
            id=idx, server_id="srv2", axis_name=ax, label=lbl, model_version="v1.0"
        )); idx += 1
    # v2 scores -- srv1 changes overall_risk MEDIUM->HIGH; srv2 unchanged
    for ax, lbl in (("overall_risk", "HIGH"), ("auth_strength", "WEAK"),
                    ("maintainer_trust", "ESTABLISHED"), ("exploit_surface", "MODERATE")):
        s.add(McpLlmAxisScore(
            id=idx, server_id="srv1", axis_name=ax, label=lbl, model_version="v2.0"
        )); idx += 1
    for ax, lbl in (("overall_risk", "LOW"), ("auth_strength", "STRONG")):
        s.add(McpLlmAxisScore(
            id=idx, server_id="srv2", axis_name=ax, label=lbl, model_version="v2.0"
        )); idx += 1
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

    # Happy path: at least one attributed axis change should be returned
    r = c.get("/api/probes/axes/attribution")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert j["total"] >= 1, f"Expected at least 1 change, got {j}"
    # srv1's overall_risk changed MEDIUM->HIGH between v1.0 and v2.0
    overall_changes = [c for c in j["changes"] if c["axis_name"] == "overall_risk" and c["server_id"] == "srv1"]
    assert len(overall_changes) >= 1, f"No overall_risk change for srv1: {j}"
    chg = overall_changes[0]
    assert chg["old_label"] == "MEDIUM", chg
    assert chg["new_label"] == "HIGH", chg
    assert chg["old_model_version"] == "v1.0", chg
    assert chg["new_model_version"] == "v2.0", chg
    assert "model_label:HIGH" in chg["attribution_sources"], chg

    # Filter by axis
    r2 = c.get("/api/probes/axes/attribution?axis=overall_risk")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert all(c["axis_name"] == "overall_risk" for c in j2["changes"]), j2

    # Filter by since_version (only changes after v1.0)
    r3 = c.get("/api/probes/axes/attribution?since_version=v1.0")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    for c3 in j3["changes"]:
        assert c3["new_model_version"] != "v1.0", f"Should not include v1.0 changes: {c3}"
        assert c3["old_model_version"] == "v1.0", c3

    # Invalid axis
    r4 = c.get("/api/probes/axes/attribution?axis=invalid_axis")
    assert r4.status_code == 400, r4.text

    # limit bounds
    r5 = c.get("/api/probes/axes/attribution?limit=0")
    assert r5.status_code == 400, r5.text

    print("PASS")
