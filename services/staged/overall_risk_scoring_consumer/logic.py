"""overall_risk_scoring_consumer logic.py"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter()


class OverallRiskResponse(BaseModel):
    server_id: str
    server_name: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    label: Optional[str] = None
    label_index: Optional[int] = None
    scored_at: Optional[datetime] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None


@router.get("/api/servers/{server_id}/overall-risk", response_model=OverallRiskResponse)
def get_overall_risk(server_id: str, session: Session = Depends(get_session)) -> OverallRiskResponse:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.label,
            s.label_index,
            s.scored_at,
            s.decision_rule_version,
            s.model_version
        FROM mcp_llm_axis_scores s
        JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.axis_name = :axis_name
          AND s.server_id = :server_id
        ORDER BY s.scored_at DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchone()
    
    if row is None:
        raise HTTPException(status_code=404, detail="Overall risk score not found")
    
    return OverallRiskResponse(
        server_id=row.server_id,
        server_name=row.server_name,
        p_top=float(row.p_top) if row.p_top is not None else None,
        p_critical=float(row.p_critical) if row.p_critical is not None else None,
        p_danger=float(row.p_danger) if row.p_danger is not None else None,
        label=row.label,
        label_index=row.label_index,
        scored_at=row.scored_at,
        decision_rule_version=row.decision_rule_version,
        model_version=row.model_version,
    )


def get_badge_data(server_id: str, session: Session) -> Dict[str, Any]:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.label,
            s.label_index,
            s.scored_at,
            s.decision_rule_version,
            s.model_version
        FROM mcp_llm_axis_scores s
        JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.axis_name = :axis_name
          AND s.server_id = :server_id
        ORDER BY s.scored_at DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchone()
    
    if row is None:
        return {}
    
    return {
        "server_id": row.server_id,
        "server_name": row.server_name,
        "p_top": float(row.p_top) if row.p_top is not None else None,
        "p_critical": float(row.p_critical) if row.p_critical is not None else None,
        "p_danger": float(row.p_danger) if row.p_danger is not None else None,
        "label": row.label,
        "label_index": row.label_index,
        "scored_at": row.scored_at,
        "decision_rule_version": row.decision_rule_version,
        "model_version": row.model_version,
    }


def query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    import requests
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}},
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def get_tier_timeline(server_id: str, session: Session) -> List[Dict[str, Any]]:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            r.risk_tier,
            s.scored_at,
            s.label,
            s.label_index
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
            AND s.axis_name = :axis_name
        WHERE r.server_id = :server_id
        ORDER BY s.scored_at DESC NULLS LAST
    """)
    rows = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchall()
    return [
        {
            "server_id": row.server_id,
            "server_name": row.server_name,
            "risk_tier": row.risk_tier,
            "scored_at": row.scored_at,
            "label": row.label,
            "label_index": row.label_index,
        }
        for row in rows
    ]


def get_trust_decay_alert(server_id: str, session: Session) -> Optional[Dict[str, Any]]:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            r.trust_score,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.label,
            s.scored_at
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
            AND s.axis_name = :axis_name
        WHERE r.server_id = :server_id
        ORDER BY s.scored_at DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchone()
    
    if row is None:
        return None
    
    return {
        "server_id": row.server_id,
        "server_name": row.server_name,
        "trust_score": row.trust_score,
        "p_top": float(row.p_top) if row.p_top is not None else None,
        "p_critical": float(row.p_critical) if row.p_critical is not None else None,
        "p_danger": float(row.p_danger) if row.p_danger is not None else None,
        "label": row.label,
        "scored_at": row.scored_at,
    }


def get_signal_scores_distribution(session: Session) -> List[Dict[str, Any]]:
    sql = text("""
        SELECT
            label,
            label_index,
            COUNT(*) AS count,
            AVG(p_top) AS avg_p_top,
            AVG(p_critical) AS avg_p_critical,
            AVG(p_danger) AS avg_p_danger
        FROM mcp_llm_axis_scores
        WHERE axis_name = :axis_name
        GROUP BY label, label_index
        ORDER BY label_index
    """)
    rows = session.execute(sql, {"axis_name": "overall_risk"}).fetchall()
    return [
        {
            "label": row.label,
            "label_index": row.label_index,
            "count": row.count,
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top is not None else None,
            "avg_p_critical": float(row.avg_p_critical) if row.avg_p_critical is not None else None,
            "avg_p_danger": float(row.avg_p_danger) if row.avg_p_danger is not None else None,
        }
        for row in rows
    ]


def get_snake_case() -> str:
    return "overall_risk_scoring_consumer"


def get_health_summary(session: Session) -> Dict[str, Any]:
    sql = text("""
        SELECT
            COUNT(DISTINCT server_id) AS total_servers,
            COUNT(*) AS total_scores,
            COUNT(DISTINCT label) AS unique_labels,
            MAX(scored_at) AS latest_score
        FROM mcp_llm_axis_scores
        WHERE axis_name = :axis_name
    """)
    row = session.execute(sql, {"axis_name": "overall_risk"}).fetchone()
    return {
        "total_servers": row.total_servers,
        "total_scores": row.total_scores,
        "unique_labels": row.unique_labels,
        "latest_score": row.latest_score,
    }


def get_threat_intel(server_id: str, session: Session) -> Optional[Dict[str, Any]]:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.label,
            s.label_index,
            s.escalated,
            s.scored_at
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
            AND s.axis_name = :axis_name
        WHERE r.server_id = :server_id
        ORDER BY s.scored_at DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchone()
    
    if row is None:
        return None
    
    return {
        "server_id": row.server_id,
        "server_name": row.server_name,
        "p_top": float(row.p_top) if row.p_top is not None else None,
        "p_critical": float(row.p_critical) if row.p_critical is not None else None,
        "p_danger": float(row.p_danger) if row.p_danger is not None else None,
        "label": row.label,
        "label_index": row.label_index,
        "escalated": row.escalated,
        "scored_at": row.scored_at,
    }


def trust_gate(server_id: str, threshold: float = 0.5, session: Session = Depends(get_session)) -> bool:
    sql = text("""
        SELECT p_top
        FROM mcp_llm_axis_scores
        WHERE axis_name = :axis_name
          AND server_id = :server_id
        ORDER BY scored_at DESC
        LIMIT 1
    """)
    row = session.execute(sql, {"axis_name": "overall_risk", "server_id": server_id}).fetchone()
    
    if row is None or row.p_top is None:
        return False
    
    return float(row.p_top) >= threshold


def get_service_health_alert_feed(session: Session, limit: int = 100) -> List[Dict[str, Any]]:
    sql = text("""
        SELECT
            r.server_id,
            r.name AS server_name,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.label,
            s.scored_at,
            r.trust_score
        FROM mcp_llm_axis_scores s
        JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.axis_name = :axis_name
        ORDER BY s.scored_at DESC
        LIMIT :limit
    """)
    rows = session.execute(sql, {"axis_name": "overall_risk", "limit": limit}).fetchall()
    return [
        {
            "server_id": row.server_id,
            "server_name": row.server_name,
            "p_top": float(row.p_top) if row.p_top is not None else None,
            "p_critical": float(row.p_critical) if row.p_critical is not None else None,
            "p_danger": float(row.p_danger) if row.p_danger is not None else None,
            "label": row.label,
            "scored_at": row.scored_at,
            "trust_score": row.trust_score,
        }
        for row in rows
    ]


def _override_get_session(test_engine):
    from app.db import get_session as _get_session
    
    def _test_session():
        return test_engine
    
    return _test_session


def test_endpoint(session_factory):
    from app.db import get_session
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    from app.db import get_session as _get_session
    app.dependency_overrides[_get_session] = session_factory
    
    client = TestClient(app)
    response = client.get("/api/servers/test-server-1/overall-risk")
    return response


def _test():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import sys
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                confidence REAL,
                scan_count INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
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
                scored_at TEXT,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT
            )
        """))
        
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, url, risk_tier, trust_score)
            VALUES ('server-001', 'Test Server Alpha', 'https://test.example.com', 'medium', 0.75)
        """))
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, scored_at, model_version, decision_rule_version)
            VALUES 
            ('server-001', 'overall_risk', 'elevated', 2, 0.35, 0.15, 0.45, '2025-01-15T10:30:00Z', 'v2.1.0', 'rule-v3')
        """))
        conn.commit()
    
    TestSession = sessionmaker(bind=engine)
    
    def _session_factory():
        return TestSession()
    
    app = FastAPI()
    app.include_router(router)
    
    from app.db import get_session as _get_session
    app.dependency_overrides[_get_session] = _session_factory
    
    client = TestClient(app)
    response = client.get("/api/servers/server-001/overall-risk")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["server_id"] == "server-001"
    assert data["server_name"] == "Test Server Alpha"
    assert data["p_top"] == 0.35
    assert data["p_critical"] == 0.15
    assert data["p_danger"] == 0.45
    assert data["label"] == "elevated"
    assert data["label_index"] == 2
    assert data["model_version"] == "v2.1.0"
    assert data["decision_rule_version"] == "rule-v3"
    assert data["scored_at"] is not None
    
    print("PASS")


if __name__ == "__main__":
    _test()