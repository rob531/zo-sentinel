"""
Network Egress Risk Consumer Service
Scoring consumer that identifies servers with elevated outbound-traffic risk.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consumer/network-egress-risk", tags=["network-egress-risk"])

P_TOP_THRESHOLD = 0.5
P_CRITICAL_THRESHOLD = 0.2
POLL_INTERVAL = 300


class ServerRiskResponse(BaseModel):
    server_id: str
    name: str
    egress_risk_score: float
    p_top: float
    p_critical: float
    flagged_at: datetime

    class Config:
        from_attributes = True


class ServerRiskDetailResponse(ServerRiskResponse):
    confidence: Optional[float] = None
    risk_tier: Optional[str] = None
    url: Optional[str] = None
    last_assessed: Optional[datetime] = None


def compute_risk_score(p_top: float, p_critical: float) -> float:
    """Compute egress risk score from probability components."""
    return p_top * 50 + p_critical * 50


def is_flagged(p_top: float, p_critical: float) -> bool:
    """Check if server meets risk flagging criteria."""
    return p_top >= P_TOP_THRESHOLD or p_critical >= P_CRITICAL_THRESHOLD


@router.get("/servers", response_model=list[ServerRiskResponse])
def get_flagged_servers(session: Session = Depends(get_session)) -> list[dict]:
    """
    List all servers flagged for elevated network egress risk.
    Returns servers where p_top >= 0.5 or p_critical >= 0.2 on network_egress axis.
    """
    query = text("""
        SELECT 
            sr.server_id,
            sr.name,
            ax.p_top,
            ax.p_critical,
            ax.scored_at as flagged_at,
            (ax.p_top * 50 + ax.p_critical * 50) as egress_risk_score
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name = 'network_egress'
          AND (ax.p_top >= :p_top_thresh OR ax.p_critical >= :p_critical_thresh)
        ORDER BY egress_risk_score DESC
    """)
    
    result = session.execute(query, {
        "p_top_thresh": P_TOP_THRESHOLD,
        "p_critical_thresh": P_CRITICAL_THRESHOLD
    })
    
    rows = result.fetchall()
    return [
        ServerRiskResponse(
            server_id=row.server_id,
            name=row.name,
            egress_risk_score=float(row.egress_risk_score),
            p_top=float(row.p_top),
            p_critical=float(row.p_critical),
            flagged_at=row.flagged_at
        )
        for row in rows
    ]


@router.get("/servers/{server_id}", response_model=ServerRiskDetailResponse)
def get_server_risk_detail(server_id: str, session: Session = Depends(get_session)) -> dict:
    """
    Get detailed risk information for a specific server.
    """
    query = text("""
        SELECT 
            sr.server_id,
            sr.name,
            sr.confidence,
            sr.risk_tier,
            sr.url,
            sr.last_assessed,
            ax.p_top,
            ax.p_critical,
            ax.scored_at as flagged_at,
            (ax.p_top * 50 + ax.p_critical * 50) as egress_risk_score
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name = 'network_egress'
          AND ax.server_id = :server_id
    """)
    
    result = session.execute(query, {"server_id": server_id})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found or has no network_egress data")
    
    return ServerRiskDetailResponse(
        server_id=row.server_id,
        name=row.name,
        egress_risk_score=float(row.egress_risk_score),
        p_top=float(row.p_top),
        p_critical=float(row.p_critical),
        flagged_at=row.flagged_at,
        confidence=float(row.confidence) if row.confidence else None,
        risk_tier=row.risk_tier,
        url=row.url,
        last_assessed=row.last_assessed
    )


def _heartbeat() -> bool:
    """Post heartbeat to service_health bus table."""
    try:
        payload = {
            "service": "network_egress_risk_consumer",
            "status": "running",
            "timestamp": datetime.utcnow().isoformat()
        }
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json={"table": "service_health", "data": payload},
            timeout=5
        )
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def write_risk_event(server_id: str, name: str, egress_risk_score: float, 
                     p_top: float, p_critical: float) -> bool:
    """Write risk event to mcp_risk_register via write_service."""
    try:
        payload = {
            "event_type": "network_egress_risk",
            "server_id": server_id,
            "server_name": name,
            "egress_risk_score": egress_risk_score,
            "p_top": p_top,
            "p_critical": p_critical,
            "flagged_at": datetime.utcnow().isoformat()
        }
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json={"table": "mcp_risk_register", "data": payload},
            timeout=5
        )
        return response.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"Failed to write risk event: {e}")
        return False


def run(session: Session) -> int:
    """
    Run the consumer once: scan for flagged servers and write risk events.
    Returns count of flagged servers processed.
    """
    query = text("""
        SELECT 
            sr.server_id,
            sr.name,
            ax.p_top,
            ax.p_critical,
            ax.scored_at as flagged_at,
            (ax.p_top * 50 + ax.p_critical * 50) as egress_risk_score
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name = 'network_egress'
          AND (ax.p_top >= :p_top_thresh OR ax.p_critical >= :p_critical_thresh)
    """)
    
    result = session.execute(query, {
        "p_top_thresh": P_TOP_THRESHOLD,
        "p_critical_thresh": P_CRITICAL_THRESHOLD
    })
    
    rows = result.fetchall()
    count = 0
    for row in rows:
        if write_risk_event(
            server_id=row.server_id,
            name=row.name,
            egress_risk_score=float(row.egress_risk_score),
            p_top=float(row.p_top),
            p_critical=float(row.p_critical)
        ):
            count += 1
    
    return count


async def run_daemon():
    """Run the consumer as a daemon, polling every 300 seconds."""
    logger.info("Starting network egress risk consumer daemon")
    while True:
        try:
            from app.db import SessionLocal
            session = SessionLocal()
            try:
                count = run(session)
                logger.info(f"Processed {count} flagged servers")
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in daemon loop: {e}")
        _heartbeat()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    SessionLocal = sessionmaker(bind=in_memory_engine)
    session = SessionLocal()
    
    session.execute(text("""
        CREATE TABLE mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            confidence REAL,
            risk_tier TEXT,
            url TEXT,
            last_assessed TIMESTAMP,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            last_scanned TIMESTAMP,
            description TEXT,
            registry_source TEXT,
            scan_count INTEGER,
            trust_score REAL,
            verdict TEXT,
            verdict_reasoning TEXT,
            meta TEXT
        )
    """))
    
    session.execute(text("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_top REAL NOT NULL,
            p_critical REAL NOT NULL,
            p_danger REAL,
            probs TEXT,
            label TEXT,
            label_index INTEGER,
            model_version TEXT,
            decision_rule_version TEXT,
            adapter_sha256 TEXT,
            scored_at TIMESTAMP NOT NULL,
            escalated INTEGER,
            escalated_to TEXT
        )
    """))
    
    critical_server = "srv-critical-001"
    warning_server = "srv-warning-002"
    clean_server = "srv-clean-003"
    now = datetime.utcnow()
    
    session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, name, confidence, risk_tier, url)
        VALUES (:server_id, :name, :confidence, :risk_tier, :url)
    """), {
        "server_id": critical_server,
        "name": "Critical Egress Server",
        "confidence": 0.9,
        "risk_tier": "high",
        "url": "https://critical.example.com"
    })
    
    session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, name, confidence, risk_tier, url)
        VALUES (:server_id, :name, :confidence, :risk_tier, :url)
    """), {
        "server_id": warning_server,
        "name": "Warning Egress Server",
        "confidence": 0.7,
        "risk_tier": "medium",
        "url": "https://warning.example.com"
    })
    
    session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, name, confidence, risk_tier, url)
        VALUES (:server_id, :name, :confidence, :risk_tier, :url)
    """), {
        "server_id": clean_server,
        "name": "Clean Egress Server",
        "confidence": 0.5,
        "risk_tier": "low",
        "url": "https://clean.example.com"
    })
    
    session.execute(text("""
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, scored_at)
        VALUES (:server_id, :axis_name, :p_top, :p_critical, :scored_at)
    """), {
        "server_id": critical_server,
        "axis_name": "network_egress",
        "p_top": 0.6,
        "p_critical": 0.3,
        "scored_at": now
    })
    
    session.execute(text("""
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, scored_at)
        VALUES (:server_id, :axis_name, :p_top, :p_critical, :scored_at)
    """), {
        "server_id": warning_server,
        "axis_name": "network_egress",
        "p_top": 0.3,
        "p_critical": 0.15,
        "scored_at": now
    })
    
    session.execute(text("""
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, scored_at)
        VALUES (:server_id, :axis_name, :p_top, :p_critical, :scored_at)
    """), {
        "server_id": clean_server,
        "axis_name": "network_egress",
        "p_top": 0.1,
        "p_critical": 0.05,
        "scored_at": now
    })
    
    session.commit()
    
    query = text("""
        SELECT 
            sr.server_id,
            sr.name,
            ax.p_top,
            ax.p_critical,
            ax.scored_at as flagged_at,
            (ax.p_top * 50 + ax.p_critical * 50) as egress_risk_score
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name = 'network_egress'
          AND (ax.p_top >= :p_top_thresh OR ax.p_critical >= :p_critical_thresh)
    """)
    
    result = session.execute(query, {
        "p_top_thresh": P_TOP_THRESHOLD,
        "p_critical_thresh": P_CRITICAL_THRESHOLD
    })
    
    rows = result.fetchall()
    flagged_count = len(rows)
    
    critical_found = False
    for row in rows:
        if row.server_id == critical_server:
            critical_found = True
            break
    
    session.close()
    
    assert flagged_count == 1, f"Expected 1 flagged server, got {flagged_count}"
    assert critical_found, f"Critical server {critical_server} not in results"
    
    print("PASS")