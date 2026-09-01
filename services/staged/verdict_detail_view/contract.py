from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["verdict_detail_view"])


class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    probs: dict[str, Any]
    scored_at: str


class VerdictDetailResponse(BaseModel):
    server_id: int
    name: str
    verdict: str
    confidence: float
    axes: list[AxisScore]
    criteria_version: str | None
    decision_rule_version: str | None


def escalate_tier(current_tier: str) -> str:
    tier_order = ["none", "low", "medium", "high", "critical"]
    try:
        idx = tier_order.index(current_tier.lower())
        if idx < len(tier_order) - 1:
            return tier_order[idx + 1]
    except ValueError:
        pass
    return current_tier


def get_verdict_detail(session: Session, server_id: int) -> VerdictDetailResponse | None:
    query = text("""
        SELECT 
            r.server_id,
            r.name,
            r.verdict,
            r.confidence,
            r.criteria_version,
            r.decision_rule_version,
            s.axis_name,
            s.label,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.escalated,
            s.probs,
            s.scored_at,
            s.overall_composite,
            s.risk_tier
        FROM McpServerRegistry r
        LEFT JOIN McpLlmAxisScore s ON r.server_id = s.server_id
        WHERE r.server_id = :server_id
        ORDER BY s.scored_at DESC NULLS LAST
    """)
    
    result = session.execute(query, {"server_id": server_id}).fetchall()
    
    if not result:
        return None
    
    first_row = result[0]
    axes: list[dict[str, Any]] = []
    effective_risk_tier = "none"
    overall_composite: float | None = None
    
    for row in result:
        if row.axis_name is not None:
            probs = row.probs if isinstance(row.probs, dict) else {}
            axes.append({
                "axis_name": row.axis_name,
                "label": row.label,
                "p_top": row.p_top,
                "p_critical": row.p_critical,
                "p_danger": row.p_danger,
                "escalated": row.escalated,
                "probs": probs,
                "scored_at": row.scored_at,
            })
            
            if row.axis_name == "overall_risk":
                overall_composite = row.overall_composite
                effective_risk_tier = row.risk_tier
    
    escalated_axes = [a for a in axes if a.get("escalated")]
    if escalated_axes:
        effective_risk_tier = escalate_tier(effective_risk_tier)
    
    return VerdictDetailResponse(
        server_id=first_row.server_id,
        name=first_row.name,
        verdict=first_row.verdict,
        confidence=first_row.confidence,
        axes=[AxisScore(**a) for a in axes],
        criteria_version=first_row.criteria_version,
        decision_rule_version=first_row.decision_rule_version,
    )


@router.get("/verdicts/{server_id}", response_model=VerdictDetailResponse)
def get_verdict(
    server_id: int,
    session: Session = Depends(get_session),
) -> VerdictDetailResponse:
    detail = get_verdict_detail(session, server_id)
    if detail is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Server not found")
    return detail


if __name__ == "__main__":
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import StaticPool
    
    create_tables = text("""
        CREATE TABLE IF NOT EXISTS McpServerRegistry (
            server_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            verdict TEXT,
            confidence REAL,
            criteria_version TEXT,
            decision_rule_version TEXT
        )
    """)
    engine.execute(create_tables)
    
    create_axis_table = text("""
        CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            axis_name TEXT,
            label TEXT,
            p_top REAL,
            p_critical REAL,
            p_danger REAL,
            escalated INTEGER,
            probs TEXT,
            scored_at TEXT,
            overall_composite REAL,
            risk_tier TEXT,
            FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id)
        )
    """)
    engine.execute(create_axis_table)
    
    seed_servers = [
        (1, "server-alpha", "compliant", 0.95, "v1.0", "rule-v1"),
        (2, "server-beta", "non_compliant", 0.72, "v1.0", "rule-v1"),
        (3, "server-gamma", "needs_review", 0.55, "v1.0", "rule-v1"),
    ]
    for s in seed_servers:
        engine.execute(
            text("INSERT INTO McpServerRegistry VALUES (?, ?, ?, ?, ?, ?)"),
            s,
        )
    
    seed_axes = [
        (1, "security_posture", "Security Posture", 0.1, 0.3, 0.6, 0, '{"low": 0.1, "medium": 0.3, "high": 0.6}', "2024-01-15T10:00:00Z", 0.25, "medium"),
        (1, "overall_risk", "Overall Risk Assessment", 0.15, 0.35, 0.5, 0, '{"low": 0.15, "medium": 0.35, "high": 0.5}', "2024-01-15T10:00:00Z", 0.40, "medium"),
        (2, "security_posture", "Security Posture", 0.6, 0.3, 0.1, 1, '{"low": 0.6, "medium": 0.3, "high": 0.1}', "2024-01-15T11:00:00Z", 0.75, "high"),
        (2, "overall_risk", "Overall Risk Assessment", 0.65, 0.25, 0.1, 0, '{"low": 0.65, "medium": 0.25, "high": 0.1}', "2024-01-15T11:00:00Z", 0.80, "high"),
        (3, "security_posture", "Security Posture", 0.3, 0.4, 0.3, 0, '{"low": 0.3, "medium": 0.4, "high": 0.3}', "2024-01-15T12:00:00Z", 0.50, "medium"),
        (3, "overall_risk", "Overall Risk Assessment", 0.35, 0.45, 0.2, 0, '{"low": 0.35, "medium": 0.45, "high": 0.2}', "2024-01-15T12:00:00Z", 0.55, "medium"),
    ]
    for a in seed_axes:
        engine.execute(
            text("INSERT INTO McpLlmAxisScore (server_id, axis_name, label, p_top, p_critical, p_danger, escalated, probs, scored_at, overall_composite, risk_tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            a,
        )
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/verdicts/1")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert "axes" in data, "Missing axes in response"
    assert len(data["axes"]) > 0, "Axes list is empty"
    
    axis_names = [a["axis_name"] for a in data["axes"]]
    assert "overall_risk" in axis_names, f"overall_risk axis not found in {axis_names}"
    
    print("PASS")