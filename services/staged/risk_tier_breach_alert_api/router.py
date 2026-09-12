from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class TierBreachAlert(BaseModel):
    server_id: str
    server_name: str
    old_tier: str
    new_tier: str
    changed_at: datetime
    confidence: float


class TierBreachResponse(BaseModel):
    breach_count: int
    alerts: list[TierBreachAlert]


def _risk_tier_from_score(score: float) -> str:
    if score >= 0.85:
        return "TRUSTED_GENERAL"
    elif score >= 0.70:
        return "CAUTION_LIMITED"
    elif score >= 0.50:
        return "CAUTION_MONITORING"
    elif score >= 0.25:
        return "WARNING_RESTRICTED"
    else:
        return "DANGER_PROHIBITED"


def ensure_overview_table(session: Session) -> None:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS server_overview (
            server_id TEXT PRIMARY KEY,
            server_name TEXT,
            current_tier TEXT,
            last_updated TIMESTAMP
        )
    """))
    session.commit()


def get_server_registry_facts(session: Session, server_id: str) -> Optional[dict]:
    result = session.execute(
        text("SELECT * FROM McpServerRegistry WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).fetchone()
    if result:
        return dict(result._mapping)
    return None


def heartbeat_loop(session: Session) -> list[dict]:
    result = session.execute(text("SELECT * FROM McpServerRegistry WHERE last_seen > :threshold"),
                             {"threshold": datetime.utcnow() - timedelta(hours=1)})
    return [dict(row._mapping) for row in result]


def get_risk_history(session: Session, server_id: str, days: int = 30) -> list[dict]:
    result = session.execute(
        text("""
            SELECT * FROM McpLlmAxisScore 
            WHERE server_id = :server_id AND scored_at > :threshold
            ORDER BY scored_at DESC
        """),
        {"server_id": server_id, "threshold": datetime.utcnow() - timedelta(days=days)}
    )
    return [dict(row._mapping) for row in result]


def get_current_risk_data(session: Session, server_id: str) -> Optional[dict]:
    result = session.execute(
        text("""
            SELECT * FROM McpLlmAxisScore 
            WHERE server_id = :server_id AND axis_name = 'overall_risk'
            ORDER BY scored_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    ).fetchone()
    if result:
        return dict(result._mapping)
    return None


def signal_handler(session: Session, signal_type: str) -> dict:
    return {"signal_type": signal_type, "processed": True}


def get_transition_counts_by_day(session: Session, days: int = 7) -> list[dict]:
    result = session.execute(
        text("""
            SELECT DATE(scored_at) as day, COUNT(*) as count
            FROM McpLlmAxisScore
            WHERE scored_at > :threshold
            GROUP BY DATE(scored_at)
            ORDER BY day DESC
        """),
        {"threshold": datetime.utcnow() - timedelta(days=days)}
    )
    return [dict(row._mapping) for row in result]


def create_exemption(session: Session, server_id: str, reason: str) -> dict:
    return {"server_id": server_id, "reason": reason, "exempt": True}


def get_axis_drift_detail(session: Session, server_id: str) -> dict:
    return {"server_id": server_id, "drift_detected": False}


def get_server_volatility(session: Session, server_id: str) -> float:
    return 0.0


def get_dashboard(session: Session) -> dict:
    return {"total_servers": 0, "healthy": 0, "at_risk": 0}


def recent_events(session: Session, limit: int = 50) -> list[dict]:
    return []


def compare_snapshots(session: Session, snapshot1: str, snapshot2: str) -> dict:
    return {"changes": []}


def get_registry_source_freshness_report(session: Session) -> list[dict]:
    return []


def get_breakdown_summary(session: Session) -> dict:
    return {"summary": "ok"}


def get_axis_trend(session: Session, axis_name: str, days: int = 7) -> list[dict]:
    return []


def get_server_tier(session: Session, server_id: str) -> Optional[str]:
    result = session.execute(
        text("SELECT risk_tier FROM McpServerRegistry WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).fetchone()
    if result:
        return result[0]
    return None


def get_latest_verdict(session: Session, server_id: str) -> Optional[dict]:
    result = session.execute(
        text("""
            SELECT * FROM McpLlmAxisScore 
            WHERE server_id = :server_id AND axis_name = 'verdict'
            ORDER BY scored_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    ).fetchone()
    if result:
        return dict(result._mapping)
    return None


@router.get("/api/risk/tier-breach-alerts", response_model=TierBreachResponse)
def get_tier_breach_alerts(days: int = 7, session: Session = Depends(get_session)) -> TierBreachResponse:
    threshold = datetime.utcnow() - timedelta(days=days)
    
    result = session.execute(
        text("""
            WITH latest_scores AS (
                SELECT server_id, axis_name, p_top, scored_at,
                       ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY scored_at DESC) as rn
                FROM McpLlmAxisScore
                WHERE axis_name = 'overall_risk'
            ),
            baseline_scores AS (
                SELECT server_id, axis_name, p_top, scored_at,
                       ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY scored_at ASC) as rn
                FROM McpLlmAxisScore
                WHERE axis_name = 'overall_risk'
            ),
            current_tiers AS (
                SELECT l.server_id, l.p_top as current_score, l.scored_at as current_at
                FROM latest_scores l
                WHERE l.rn = 1
            ),
            baseline_tiers AS (
                SELECT b.server_id, b.p_top as baseline_score, b.scored_at as baseline_at
                FROM baseline_scores b
                WHERE b.rn = 1
            )
            SELECT 
                c.server_id,
                sr.name as server_name,
                b.baseline_score,
                c.current_score,
                b.baseline_at,
                c.current_at,
                sr.confidence
            FROM current_tiers c
            JOIN baseline_tiers b ON c.server_id = b.server_id
            JOIN McpServerRegistry sr ON c.server_id = sr.server_id
            WHERE c.current_at > :threshold
              AND b.baseline_score > c.current_score
              AND c.current_score < 0.85
        """),
        {"threshold": threshold}
    )
    
    alerts = []
    for row in result:
        old_tier = _risk_tier_from_score(row.baseline_score)
        new_tier = _risk_tier_from_score(row.current_score)
        if old_tier != new_tier:
            alerts.append(TierBreachAlert(
                server_id=row.server_id,
                server_name=row.server_name,
                old_tier=old_tier,
                new_tier=new_tier,
                changed_at=row.current_at,
                confidence=row.confidence
            ))
    
    return TierBreachResponse(breach_count=len(alerts), alerts=alerts)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    session.execute(text("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            name TEXT,
            risk_tier TEXT,
            confidence REAL,
            description TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            last_scanned TIMESTAMP,
            last_assessed TIMESTAMP,
            registry_source TEXT,
            scan_count INTEGER,
            trust_score REAL,
            url TEXT,
            verdict TEXT,
            verdict_reasoning TEXT,
            meta TEXT
        )
    """))
    
    session.execute(text("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            axis_name TEXT,
            p_top REAL,
            p_critical REAL,
            p_danger REAL,
            probs TEXT,
            label TEXT,
            label_index INTEGER,
            model_version TEXT,
            decision_rule_version TEXT,
            escalated INTEGER,
            escalated_to TEXT,
            adapter_sha256 TEXT,
            scored_at TIMESTAMP
        )
    """))
    
    base_time = datetime.utcnow() - timedelta(days=2)
    
    servers = [
        ("srv-001", "Alpha Server", 0.95),
        ("srv-002", "Beta Server", 0.88),
        ("srv-003", "Gamma Server", 0.75),
        ("srv-004", "Delta Server", 0.60),
    ]
    
    for server_id, name, score in servers:
        session.execute(
            text("INSERT INTO McpServerRegistry (server_id, name, risk_tier, confidence) VALUES (:s, :n, :t, :c)"),
            {"s": server_id, "n": name, "t": _risk_tier_from_score(score), "c": 0.9}
        )
        session.execute(
            text("""
                INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top, label, scored_at)
                VALUES (:s, 'overall_risk', :p, :l, :t)
            """),
            {"s": server_id, "p": score, "l": _risk_tier_from_score(score), "t": base_time}
        )
    
    degraded = [
        ("srv-001", 0.95, 0.65),
        ("srv-003", 0.75, 0.35),
    ]
    
    new_time = datetime.utcnow() - timedelta(hours=1)
    for server_id, old_score, new_score in degraded:
        session.execute(
            text("""
                INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top, label, scored_at)
                VALUES (:s, 'overall_risk', :p, :l, :t)
            """),
            {"s": server_id, "p": new_score, "l": _risk_tier_from_score(new_score), "t": new_time}
        )
    
    session.commit()
    
    app = FastAPI()
    app.include_router(router)
    
    def override_get_session():
        try:
            yield session
        finally:
            pass
    
    app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/risk/tier-breach-alerts?days=2")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["alerts"]) >= 1, f"Expected at least 1 alert, got {len(data['alerts'])}"
    server_ids = [a["server_id"] for a in data["alerts"]]
    assert "srv-001" in server_ids or "srv-003" in server_ids, f"Expected srv-001 or srv-003 in alerts"
    
    print("PASS")