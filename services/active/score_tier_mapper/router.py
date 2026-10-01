# deps: fastapi, pydantic, sqlalchemy, requests
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func
import requests

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["score-tier-mapper"])

AXIS_WEIGHTS = {
    "integrity": 0.20,
    "safety": 0.18,
    "availability": 0.15,
    "performance": 0.12,
    "reliability": 0.15,
    "security": 0.12,
    "privacy": 0.08,
}

RISK_TIERS = [
    (75, "TRUSTED_GENERAL"),
    (60, "TRUSTED_RESEARCH"),
    (45, "ENTERPRISE_CONTROLLED"),
    (30, "CAUTION_LIMITED"),
    (15, "HIGH_RISK_ISOLATED"),
    (0, "KNOWN_THREAT"),
]


class AxisData(BaseModel):
    label: str
    p_top: float
    p_critical: float


class ScoreTierResponse(BaseModel):
    server_id: str
    axes: dict[str, AxisData]
    composite: float
    risk_tier: str
    scored_at: str


class OverviewResponse(BaseModel):
    total_servers: int
    tier_breakdown: dict[str, int]


def map_composite_to_tier(composite: float) -> str:
    for threshold, tier in RISK_TIERS:
        if composite > threshold:
            return tier
    return "KNOWN_THREAT"


def compute_composite(session: Session, server_id: str):
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    results = session.execute(stmt).scalars().all()
    if not results:
        return None, {}, None
    weighted_sum = 0.0
    total_weight = 0.0
    axes = {}
    scored_at = None
    for row in results:
        axis = row.axis_name
        weight = AXIS_WEIGHTS.get(axis, 0.0)
        if weight > 0:
            weighted_sum += row.p_top * weight
            total_weight += weight
        axes[axis] = AxisData(label=row.label, p_top=row.p_top, p_critical=row.p_critical)
        if scored_at is None or (row.scored_at and row.scored_at > scored_at):
            scored_at = row.scored_at
    if total_weight == 0:
        return None, {}, None
    composite = weighted_sum / total_weight
    return composite, axes, scored_at


def get_score_tier(session: Session, server_id: str):
    composite, axes, scored_at = compute_composite(session, server_id)
    if composite is None:
        return None
    risk_tier = map_composite_to_tier(composite)
    return composite, axes, risk_tier, scored_at


@router.get("/servers/{server_id}/score-tier", response_model=ScoreTierResponse)
def get_server_score_tier(server_id: str, session: Session = Depends(get_session)):
    result = get_score_tier(session, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No axis scores found for server")
    composite, axes, risk_tier, scored_at = result
    return ScoreTierResponse(
        server_id=server_id,
        axes=axes,
        composite=round(composite, 4),
        risk_tier=risk_tier,
        scored_at=str(scored_at) if scored_at else "",
    )


@router.get("/score-tier/overview", response_model=OverviewResponse)
def get_score_tier_overview(session: Session = Depends(get_session)):
    stmt = select(func.count(func.distinct(McpLlmAxisScore.server_id)))
    total = session.execute(stmt).scalar() or 0
    tier_breakdown = {}
    if total > 0:
        stmt = select(McpLlmAxisScore.server_id).distinct()
        server_ids = [r[0] for r in session.execute(stmt).all()]
        for sid in server_ids:
            result = get_score_tier(session, sid)
            if result:
                _, _, risk_tier, _ = result
                tier_breakdown[risk_tier] = tier_breakdown.get(risk_tier, 0) + 1
    return OverviewResponse(total_servers=total, tier_breakdown=tier_breakdown)


def _write_risk_tier(server_id: str, risk_tier: str, timeout: float = 5.0) -> bool:
    payload = {
        "table": "mcp_server_registry",
        "records": [{"server_id": server_id, "risk_tier": risk_tier}],
    }
    try:
        resp = requests.post("http://127.0.0.1:8772/write", json=payload, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


@router.post("/servers/{server_id}/score-tier")
def upsert_server_risk_tier(server_id: str, session: Session = Depends(get_session)):
    result = get_score_tier(session, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No axis scores found for server")
    _, _, risk_tier, _ = result
    _write_risk_tier(server_id, risk_tier)
    return {"server_id": server_id, "risk_tier": risk_tier, "status": "upserted"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime, timedelta

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE mcp_llm_axis_scores ("
                "id INTEGER PRIMARY KEY, server_id TEXT NOT NULL, "
                "axis_name TEXT NOT NULL, label TEXT, p_top REAL, "
                "p_critical REAL, scored_at TEXT)"
            )
        )
        conn.execute(
            text("CREATE TABLE mcp_server_registry (server_id TEXT PRIMARY KEY, risk_tier TEXT)")
        )
        conn.commit()

    scored_at = (datetime.now() - timedelta(hours=1)).isoformat()
    axis_data = [
        ("integrity", 0.20),
        ("safety", 0.18),
        ("availability", 0.15),
        ("performance", 0.12),
        ("reliability", 0.15),
        ("security", 0.12),
        ("privacy", 0.08),
    ]

    with engine.begin() as conn:
        # server-a: composite 76.875 -> TRUSTED_GENERAL (>75)
        pts_a = [90.0, 80.0, 70.0, 60.0, 85.0, 75.0, 65.0]
        for (ax, _), p in zip(axis_data, pts_a):
            conn.execute(
                text(
                    "INSERT INTO mcp_llm_axis_scores "
                    "(server_id, axis_name, label, p_top, p_critical, scored_at) "
                    "VALUES (:sid, :ax, :lbl, :pt, :pc, :sa)"
                ),
                {"sid": "server-a", "ax": ax, "lbl": "high", "pt": p, "pc": 0.0, "sa": scored_at},
            )
        # server-b: composite 25.0 -> HIGH_RISK_ISOLATED (>15, <=30)
        pts_b = [30.0, 25.0, 20.0, 35.0, 40.0, 15.0, 10.0]
        for (ax, _), p in zip(axis_data, pts_b):
            conn.execute(
                text(
                    "INSERT INTO mcp_llm_axis_scores "
                    "(server_id, axis_name, label, p_top, p_critical, scored_at) "
                    "VALUES (:sid, :ax, :lbl, :pt, :pc, :sa)"
                ),
                {"sid": "server-b", "ax": ax, "lbl": "medium", "pt": p, "pc": 0.0, "sa": scored_at},
            )
        # server-c: composite 10.0 -> KNOWN_THREAT (<=15)
        pts_c = [12.0, 10.0, 8.0, 15.0, 18.0, 5.0, 3.0]
        for (ax, _), p in zip(axis_data, pts_c):
            conn.execute(
                text(
                    "INSERT INTO mcp_llm_axis_scores "
                    "(server_id, axis_name, label, p_top, p_critical, scored_at) "
                    "VALUES (:sid, :ax, :lbl, :pt, :pc, :sa)"
                ),
                {"sid": "server-c", "ax": ax, "lbl": "low", "pt": p, "pc": 0.0, "sa": scored_at},
            )

    the_app = FastAPI()
    the_app.include_router(router)
    the_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(the_app, raise_server_exceptions=False)

    for sid in ["server-a", "server-b", "server-c"]:
        resp = client.post(f"/api/servers/{sid}/score-tier")
        assert resp.status_code == 200, f"write failed for {sid}: {resp.text}"

    resp_a = client.get("/api/servers/server-a/score-tier")
    assert resp_a.status_code == 200, f"GET failed: {resp_a.text}"
    assert resp_a.json()["risk_tier"] == "TRUSTED_GENERAL", f"server-a tier: {resp_a.json().get('risk_tier')}"

    resp_b = client.get("/api/servers/server-b/score-tier")
    assert resp_b.status_code == 200
    assert resp_b.json()["risk_tier"] == "HIGH_RISK_ISOLATED", f"server-b tier: {resp_b.json().get('risk_tier')}"

    resp_c = client.get("/api/servers/server-c/score-tier")
    assert resp_c.status_code == 200
    assert resp_c.json()["risk_tier"] == "KNOWN_THREAT", f"server-c tier: {resp_c.json().get('risk_tier')}"

    resp_ov = client.get("/api/score-tier/overview")
    assert resp_ov.status_code == 200
    ov = resp_ov.json()
    assert ov["total_servers"] == 3, f"total_servers: {ov.get('total_servers')}"
    assert ov["tier_breakdown"].get("TRUSTED_GENERAL", 0) == 1, f"tier_breakdown: {ov.get('tier_breakdown')}"
    assert ov["tier_breakdown"].get("HIGH_RISK_ISOLATED", 0) == 1
    assert ov["tier_breakdown"].get("KNOWN_THREAT", 0) == 1

    print("PASS")
