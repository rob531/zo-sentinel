from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict"])


class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: list[float]


class VerdictAxesResponse(BaseModel):
    axes: list[AxisScore]
    overall_risk_tier: str
    server_name: str
    criteria_version: str
    scored_at: datetime


class VerdictDetailResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: str
    trust_score: float
    confidence: float
    overall_risk_tier: str


def get_verdict_detail(session, server_id: str):
    """Get basic verdict info for a server."""
    result = session.execute(
        text("""
            SELECT msr.server_id, msr.name, msr.risk_tier, msr.trust_score, msr.confidence
            FROM mcp_server_registry msr
            WHERE msr.server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Server not found")
    
    return VerdictDetailResponse(
        server_id=row[0],
        server_name=row[1],
        risk_tier=row[2] or "unknown",
        trust_score=row[3] or 0.0,
        confidence=row[4] or 0.0,
        overall_risk_tier=row[2] or "unknown"
    )


def get_verdict_axes(session, server_id: str) -> VerdictAxesResponse:
    """Get all axis scores for a server."""
    result = session.execute(
        text("""
            SELECT 
                mla.server_id, mla.axis_name, mla.label, mla.label_index,
                mla.p_top, mla.p_critical, mla.p_danger, mla.probs,
                mla.decision_rule_version, mla.scored_at,
                msr.name as server_name, msr.risk_tier
            FROM mcp_llm_axis_scores mla
            JOIN mcp_server_registry msr ON mla.server_id = msr.server_id
            WHERE mla.server_id = :server_id
            ORDER BY mla.label_index
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    
    axes = []
    overall_risk_tier = "unknown"
    criteria_version = "unknown"
    scored_at = datetime.now()
    server_name = ""
    
    for row in rows:
        if row[1] == "overall_risk":
            overall_risk_tier = row[2] or "unknown"
            criteria_version = row[8] or "unknown"
            scored_at = row[9] or datetime.now()
            server_name = row[10]
        probs_val = row[7]
        if isinstance(probs_val, str):
            probs_list = [float(x.strip()) for x in probs_val.strip("[]").split(",") if x.strip()]
        else:
            probs_list = list(probs_val) if probs_val else []
        
        axes.append(AxisScore(
            axis_name=row[1],
            label=row[2] or "",
            label_index=row[3],
            p_top=row[4] or 0.0,
            p_critical=row[5] or 0.0,
            p_danger=row[6] or 0.0,
            probs=probs_list
        ))
    
    if not server_name:
        server_name = rows[0][10] if rows else ""
    
    return VerdictAxesResponse(
        axes=axes,
        overall_risk_tier=overall_risk_tier,
        server_name=server_name,
        criteria_version=criteria_version,
        scored_at=scored_at
    )


@router.get("/verdicts/{server_id}", response_model=VerdictDetailResponse)
def get_verdict(server_id: str, session=Depends(get_session)):
    return get_verdict_detail(session, server_id)


@router.get("/verdicts/{server_id}/axes", response_model=VerdictAxesResponse)
def get_axes(server_id: str, session=Depends(get_session)):
    return get_verdict_axes(session, server_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR(255) PRIMARY KEY,
                name VARCHAR(255),
                url VARCHAR(500),
                risk_tier VARCHAR(50),
                trust_score FLOAT,
                confidence FLOAT,
                description TEXT,
                registry_source VARCHAR(100),
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(255),
                axis_name VARCHAR(100),
                label VARCHAR(100),
                label_index INTEGER,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                probs TEXT,
                decision_rule_version VARCHAR(50),
                model_version VARCHAR(50),
                adapter_sha256 VARCHAR(100),
                scored_at TIMESTAMP,
                escalated BOOLEAN,
                escalated_to VARCHAR(100)
            )
        """))
        conn.commit()
        
        for i in range(1, 3):
            server_id = f"server_{i}"
            risk_tier = "high" if i == 1 else "medium"
            conn.execute(text("""
                INSERT INTO mcp_server_registry 
                (server_id, name, risk_tier, trust_score, confidence)
                VALUES (:server_id, :name, :risk_tier, :trust_score, :confidence)
            """), {
                "server_id": server_id,
                "name": f"Test Server {i}",
                "risk_tier": risk_tier,
                "trust_score": 0.8 if i == 1 else 0.6,
                "confidence": 0.9 if i == 1 else 0.7
            })
            
            axis_names = ['security', 'reliability', 'cost', 'performance', 'scalability', 'usability', 'overall_risk']
            for j, axis in enumerate(axis_names):
                p_top_val = 0.8 + (j * 0.02)
                probs_str = f"[{p_top_val}, {1-p_top_val}]"
                conn.execute(text("""
                    INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, decision_rule_version, model_version, scored_at)
                    VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :dr_version, :mv, :scored_at)
                """), {
                    "server_id": server_id,
                    "axis_name": axis,
                    "label": risk_tier,
                    "label_index": j,
                    "p_top": p_top_val,
                    "p_critical": 0.05,
                    "p_danger": 0.1,
                    "probs": probs_str,
                    "dr_version": "v1.0",
                    "mv": "llm-v1",
                    "scored_at": datetime.now()
                })
            conn.commit()
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    r1 = client.get("/api/verdicts/server_1")
    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"
    assert r1.json()["risk_tier"] == "high"
    
    r2 = client.get("/api/verdicts/server_2/axes")
    assert r2.status_code == 200, f"Expected 200, got {r2.status_code}"
    axes_data = r2.json()["axes"]
    assert len(axes_data) == 7, f"Expected 7 axes, got {len(axes_data)}"
    
    for i, axis in enumerate(axes_data):
        expected_p_top = 0.8 + (i * 0.02)
        assert axis["p_top"] == expected_p_top, f"Axis {i}: expected p_top {expected_p_top}, got {axis['p_top']}"
    
    print("PASS")