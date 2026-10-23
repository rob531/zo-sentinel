"""
Server Verdict Detail API - Returns detailed axis-level risk scores for a server.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["servers"])


class AxisDetail(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: str


class ServerVerdictDetailResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str]
    verdict: str
    verdict_reasoning: str
    confidence: float
    trust_score: float
    criteria_version: Optional[str]
    axes: list[AxisDetail]


def get_server_verdict_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """
    Fetch detailed axis-level risk scores for a server.
    
    Reads from mcp_llm_axis_scores joined to mcp_server_registry by server_id,
    aggregates all 7 axis rows with probabilities and metadata.
    """
    # Query server registry for basic info
    server_query = text("""
        SELECT 
            server_id,
            name,
            risk_tier,
            verdict,
            verdict_reasoning,
            confidence,
            trust_score,
            meta
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    
    server_result = db.execute(server_query, {"server_id": server_id}).fetchone()
    
    if server_result is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    # Unpack server data
    (
        s_server_id,
        name,
        risk_tier,
        verdict,
        verdict_reasoning,
        confidence,
        trust_score,
        meta,
    ) = server_result
    
    # Query all axis scores for this server
    axis_query = text("""
        SELECT 
            axis_name,
            label,
            label_index,
            p_top,
            p_critical,
            p_danger,
            probs,
            escalated,
            decision_rule_version,
            model_version,
            scored_at
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY scored_at DESC, axis_name
    """)
    
    axis_results = db.execute(axis_query, {"server_id": server_id}).fetchall()
    
    # Get criteria_version from overall_risk axis
    criteria_version = None
    axes = []
    
    for row in axis_results:
        (
            axis_name,
            label,
            label_index,
            p_top,
            p_critical,
            p_danger,
            probs,
            escalated,
            decision_rule_version,
            model_version,
            scored_at,
        ) = row
        
        if axis_name == "overall_risk" and criteria_version is None:
            criteria_version = decision_rule_version
        
        axes.append({
            "axis_name": axis_name,
            "label": label or "unknown",
            "label_index": label_index or 0,
            "p_top": p_top or 0.0,
            "p_critical": p_critical or 0.0,
            "p_danger": p_danger or 0.0,
            "escalated": bool(escalated),
            "scored_at": scored_at.isoformat() if scored_at else "",
        })
    
    # Deduplicate axes by axis_name, keeping the most recent
    seen_axes = {}
    for axis in axes:
        aname = axis["axis_name"]
        if aname not in seen_axes:
            seen_axes[aname] = axis
    axes = list(seen_axes.values())
    
    return {
        "server_id": s_server_id,
        "name": name or "unknown",
        "risk_tier": risk_tier,
        "verdict": verdict or "unknown",
        "verdict_reasoning": verdict_reasoning or "",
        "confidence": confidence or 0.0,
        "trust_score": trust_score or 0.0,
        "criteria_version": criteria_version,
        "axes": axes,
    }


@router.get("/servers/{server_id}/verdict-detail", response_model=ServerVerdictDetailResponse)
def get_verdict_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVerdictDetailResponse:
    """Get detailed verdict information for a specific server."""
    data = get_server_verdict_detail(server_id, db)
    return ServerVerdictDetailResponse(**data)


if __name__ == "__main__":
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timezone
    
    # In-memory SQLite for self-test
    engine = create_engine("sqlite:///:memory:", echo=False)
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                confidence REAL,
                trust_score REAL,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                escalated INTEGER,
                decision_rule_version TEXT,
                model_version TEXT,
                scored_at TIMESTAMP
            )
        """))
        conn.commit()
    
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    # Seed 3 servers with varied axis scores and risk_tiers
    servers = [
        ("srv-001", "High Risk Server", "high", "reject", "High risk due to multiple critical factors", 0.92, 15.0),
        ("srv-002", "Medium Risk Server", "medium", "review", "Medium risk with some concerns", 0.75, 55.0),
        ("srv-003", "Low Risk Server", "low", "approve", "Low risk, minimal concerns", 0.95, 92.0),
    ]
    
    axes_to_seed = [
        "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"
    ]
    
    labels = ["critical", "high", "medium", "low", "minimal"]
    now = datetime.now(timezone.utc)
    
    for server_id, name, risk_tier, verdict, reasoning, confidence, trust_score in servers:
        db.execute(text("""
            INSERT INTO mcp_server_registry 
            (server_id, name, risk_tier, verdict, verdict_reasoning, confidence, trust_score, meta)
            VALUES (:server_id, :name, :risk_tier, :verdict, :reasoning, :confidence, :trust_score, :meta)
        """), {
            "server_id": server_id,
            "name": name,
            "risk_tier": risk_tier,
            "verdict": verdict,
            "reasoning": reasoning,
            "confidence": confidence,
            "trust_score": trust_score,
            "meta": "{}"
        })
        
        for i, axis_name in enumerate(axes_to_seed):
            # Vary the scores based on risk_tier
            if risk_tier == "high":
                p_top, p_critical = 0.15 + (i * 0.02), 0.45 + (i * 0.03)
                label_idx = 0 if axis_name == "overall_risk" else 1
            elif risk_tier == "medium":
                p_top, p_critical = 0.25 + (i * 0.02), 0.35 + (i * 0.02)
                label_idx = 2
            else:
                p_top, p_critical = 0.55 + (i * 0.03), 0.20 + (i * 0.02)
                label_idx = 3 if axis_name != "overall_risk" else 4
            
            db.execute(text("""
                INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated, decision_rule_version, model_version, scored_at)
                VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :escalated, :dr_version, :model_version, :scored_at)
            """), {
                "server_id": server_id,
                "axis_name": axis_name,
                "label": labels[label_idx],
                "label_index": label_idx,
                "p_top": p_top,
                "p_critical": p_critical,
                "p_danger": 0.25,
                "probs": json.dumps([p_top, p_critical, 0.25, 0.15]),
                "escalated": 1 if risk_tier == "high" and axis_name == "overall_risk" else 0,
                "dr_version": "rules-v2.1.0" if risk_tier == "high" else "rules-v2.0.0",
                "model_version": "gpt-4o-2024-05-13",
                "scored_at": now
            })
    
    db.commit()
    
    # Run self-test
    from fastapi.testclient import TestClient
    from main import app
    
    client = TestClient(app)
    
    all_passed = True
    
    for server_id, _, expected_tier, _, _, _, _ in servers:
        response = client.get(f"/api/servers/{server_id}/verdict-detail")
        
        if response.status_code != 200:
            print(f"FAIL: Expected 200 for {server_id}, got {response.status_code}")
            all_passed = False
            continue
        
        data = response.json()
        
        if len(data.get("axes", [])) < 7:
            print(f"FAIL: Expected >= 7 axes for {server_id}, got {len(data.get('axes', []))}")
            all_passed = False
            continue
        
        if data.get("risk_tier") is None:
            print(f"FAIL: risk_tier should not be null for {server_id}")
            all_passed = False
            continue
        
        print(f"PASS: {server_id} - {data.get('name')} - {data.get('risk_tier')} - {len(data.get('axes', []))} axes")
    
    if all_passed:
        print("PASS")
    else:
        print("FAIL")
    
    db.close()