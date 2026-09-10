"""
compare_risk_tiers router
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional, Dict, Any
from datetime import datetime
import time
import json

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/compare", tags=["comparison"])

_rate_limits: Dict[str, list] = {}

def _rate_check(identifier: str, limit: int = 100, window: int = 60) -> None:
    now = time.time()
    if identifier not in _rate_limits:
        _rate_limits[identifier] = []
    _rate_limits[identifier] = [t for t in _rate_limits[identifier] if now - t < window]
    if len(_rate_limits[identifier]) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_limits[identifier].append(now)


def _build_risk_comparison(server_ids: List[str], db: Session) -> Dict[str, Any]:
    servers = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id.in_(server_ids)
    ).all()

    server_map = {s.server_id: s for s in servers}

    risk_tiers = {}
    for sid in server_ids:
        if sid not in server_map:
            risk_tiers[sid] = None
            continue
        tier = server_map[sid].risk_tier
        risk_tiers[sid] = tier or "unknown"

    tier_counts: Dict[str, int] = {}
    for t in risk_tiers.values():
        tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "servers": servers,
        "server_map": server_map,
        "risk_tiers": risk_tiers,
        "tier_counts": tier_counts,
    }


def get_risk_tier_comparison(server_ids: List[str], db: Session) -> Dict[str, Any]:
    data = _build_risk_comparison(server_ids, db)

    servers = data["servers"]
    server_map = data["server_map"]
    risk_tiers = data["risk_tiers"]
    tier_counts = data["tier_counts"]

    if not servers:
        return {
            "server_ids": server_ids,
            "count": 0,
            "risk_tiers": {},
            "tier_counts": {},
            "summary": {},
            "visualization": {},
        }

    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id.in_(server_ids)
    ).all()

    axis_by_server: Dict[str, List] = {}
    for ax in axis_scores:
        if ax.server_id not in axis_by_server:
            axis_by_server[ax.server_id] = []
        axis_by_server[ax.server_id].append({
            "axis_name": ax.axis_name,
            "label": ax.label,
            "p_critical": ax.p_critical,
            "p_danger": ax.p_danger,
            "p_top": ax.p_top,
            "model_version": ax.model_version,
            "scored_at": ax.scored_at.isoformat() if ax.scored_at else None,
        })

    server_comparisons = []
    for sid in server_ids:
        if sid not in server_map:
            continue
        s = server_map[sid]
        trust_scores = []
        confidences = []
        verdicts = []
        if s.trust_score is not None:
            trust_scores.append(s.trust_score)
        if s.confidence is not None:
            confidences.append(s.confidence)
        if s.verdict is not None:
            verdicts.append(s.verdict)

        server_comparisons.append({
            "server_id": sid,
            "name": s.name,
            "risk_tier": s.risk_tier or "unknown",
            "trust_score": s.trust_score,
            "confidence": s.confidence,
            "verdict": s.verdict,
            "verdict_reasoning": s.verdict_reasoning,
            "url": s.url,
            "registry_source": s.registry_source,
            "last_scanned": s.last_scanned.isoformat() if s.last_scanned else None,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
            "axis_scores": axis_by_server.get(sid, []),
            "metrics": {
                "trust_score": s.trust_score,
                "confidence": s.confidence,
                "scan_count": s.scan_count,
                "has_threat": bool(s.verdict == "threat"),
            },
        })

    sorted_by_trust = sorted(
        [(s["server_id"], s["trust_score"]) for s in server_comparisons if s["trust_score"] is not None],
        key=lambda x: x[1],
        reverse=True,
    )

    trust_values = [v for _, v in sorted_by_trust]
    avg_trust = sum(trust_values) / len(trust_values) if trust_values else 0

    summary = {
        "total_servers": len(server_comparisons),
        "tier_distribution": tier_counts,
        "avg_trust_score": round(avg_trust, 4),
        "lowest_trust_server": sorted_by_trust[-1][0] if sorted_by_trust else None,
        "highest_trust_server": sorted_by_trust[0][0] if sorted_by_trust else None,
    }

    viz_bars = []
    max_count = max(tier_counts.values()) if tier_counts else 1
    for tier in ["critical", "high", "medium", "low", "unknown"]:
        cnt = tier_counts.get(tier, 0)
        pct = cnt / max_count if max_count > 0 else 0
        bar_len = int(pct * 20)
        viz_bars.append(f"{tier:10} | {'#' * bar_len} {cnt}")

    visualization = {
        "risk_distribution": "\n".join(viz_bars),
        "sorted_by_trust": [sid for sid, _ in sorted_by_trust],
    }

    return {
        "server_ids": server_ids,
        "count": len(server_comparisons),
        "risk_tiers": risk_tiers,
        "tier_counts": tier_counts,
        "server_comparisons": server_comparisons,
        "summary": summary,
        "visualization": visualization,
    }


@router.get("/risk_tiers")
async def compare_risk_tiers(
    request: Request,
    server_ids: List[str] = Query(..., min_length=1, description="List of server IDs to compare"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    _rate_check(request.client.host, limit=100, window=60)

    if len(server_ids) > limit:
        raise HTTPException(status_code=400, detail=f"Exceeds limit of {limit} servers per request")

    if not all(server_ids):
        raise HTTPException(status_code=400, detail="server_ids cannot contain empty values")

    result = get_risk_tier_comparison(server_ids, db)

    return {
        "status": "success",
        "requested_servers": len(server_ids),
        "matched_servers": result["count"],
        "server_ids": server_ids,
        "risk_tiers": result["risk_tiers"],
        "tier_counts": result["tier_counts"],
        "server_comparisons": result["server_comparisons"],
        "summary": result["summary"],
        "visualization": result["visualization"],
        "meta": {
            "timestamp": datetime.utcnow().isoformat(),
        },
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    session = TestingSession()
    session.add(McpServerRegistry(
        server_id="srv1", name="Server One", risk_tier="low",
        trust_score=0.9, confidence=0.85, verdict="safe", registry_source="test"
    ))
    session.add(McpServerRegistry(
        server_id="srv2", name="Server Two", risk_tier="high",
        trust_score=0.3, confidence=0.6, verdict="threat", registry_source="test"
    ))
    session.add(McpServerRegistry(
        server_id="srv3", name="Server Three", risk_tier="medium",
        trust_score=0.6, confidence=0.7, verdict="unknown", registry_source="test"
    ))
    session.add(McpLlmAxisScore(
        server_id="srv1", axis_name="safety", label="safe",
        p_critical=0.05, p_danger=0.1, p_top=0.85, model_version="v1"
    ))
    session.add(McpLlmAxisScore(
        server_id="srv2", axis_name="safety", label="danger",
        p_critical=0.6, p_danger=0.3, p_top=0.1, model_version="v1"
    ))
    session.commit()
    session.close()

    with engine.connect() as conn:
        from fastapi.testclient import TestClient
        client = TestClient(app)

        resp = client.get("/compare/risk_tiers?server_ids=srv1&server_ids=srv2")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert data["status"] == "success"
        assert data["matched_servers"] == 2
        assert "srv1" in data["risk_tiers"]
        assert "srv2" in data["risk_tiers"]
        assert data["tier_counts"]["low"] == 1
        assert data["tier_counts"]["high"] == 1
        assert "server_comparisons" in data
        assert len(data["server_comparisons"]) == 2
        assert "visualization" in data
        assert "risk_distribution" in data["visualization"]

        comp1 = next(c for c in data["server_comparisons"] if c["server_id"] == "srv1")
        assert comp1["risk_tier"] == "low"
        assert comp1["trust_score"] == 0.9
        assert len(comp1["axis_scores"]) == 1

        resp3 = client.get("/compare/risk_tiers?server_ids=srv1&server_ids=srv3")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["tier_counts"]["medium"] == 1

        resp_err = client.get("/compare/risk_tiers?server_ids=srv999")
        assert resp_err.status_code == 200
        assert resp_err.json()["matched_servers"] == 0

        resp_bad = client.get("/compare/risk_tiers?server_ids=")
        assert resp_bad.status_code == 422

    print("PASS")