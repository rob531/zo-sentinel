# services/staged/axis_score_drift/logic.py
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def ensure_tables(session: Session) -> None:
    """Create scoring_axis_drift table if not exists."""
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS scoring_axis_drift (
            id SERIAL PRIMARY KEY,
            server_id VARCHAR(255) NOT NULL,
            axis_name VARCHAR(255) NOT NULL,
            prev_p_critical FLOAT,
            curr_p_critical FLOAT,
            prev_p_top FLOAT,
            curr_p_top FLOAT,
            drift_critical FLOAT NOT NULL,
            drift_top FLOAT NOT NULL,
            scored_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_drift_server ON scoring_axis_drift(server_id)
    """))
    session.commit()


def compute_drift(session: Session, days: int = 7) -> dict:
    """Compute axis drift for all servers and return summary."""
    ensure_tables(session)
    
    now = datetime.utcnow()
    days_ago = now - timedelta(days=days)
    
    servers = session.query(McpServerRegistry).all()
    total_servers = len(servers)
    
    drift_summary = []
    
    for server in servers:
        current_scores = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == server.server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .first()
        )
        
        historical_scores = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == server.server_id)
            .filter(McpLlmAxisScore.scored_at <= days_ago)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .first()
        )
        
        if not current_scores or not historical_scores:
            continue
        
        all_current = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == server.server_id)
            .filter(McpLlmAxisScore.scored_at >= current_scores.scored_at - timedelta(hours=1))
            .all()
        )
        
        axes = []
        max_drift = 0.0
        
        for curr in all_current:
            hist = (
                session.query(McpLlmAxisScore)
                .filter(McpLlmAxisScore.server_id == server.server_id)
                .filter(McpLlmAxisScore.axis_name == curr.axis_name)
                .filter(McpLlmAxisScore.scored_at <= days_ago)
                .order_by(McpLlmAxisScore.scored_at.desc())
                .first()
            )
            
            if hist:
                drift_critical = abs(curr.p_critical - hist.p_critical)
                drift_top = abs(curr.p_top - hist.p_top)
                
                if drift_critical > 0 or drift_top > 0:
                    axes.append({
                        "axis_name": curr.axis_name,
                        "drift_critical": round(drift_critical, 4),
                        "drift_top": round(drift_top, 4),
                    })
                    
                    curr_max = max(drift_critical, drift_top)
                    if curr_max > max_drift:
                        max_drift = curr_max
                    
                    session.execute(
                        text("""
                            INSERT INTO scoring_axis_drift 
                            (server_id, axis_name, prev_p_critical, curr_p_critical, 
                             prev_p_top, curr_p_top, drift_critical, drift_top, scored_at)
                            VALUES (:server_id, :axis_name, :prev_pc, :curr_pc,
                                    :prev_pt, :curr_pt, :drift_c, :drift_t, :scored_at)
                        """),
                        {
                            "server_id": server.server_id,
                            "axis_name": curr.axis_name,
                            "prev_pc": hist.p_critical,
                            "curr_pc": curr.p_critical,
                            "prev_pt": hist.p_top,
                            "curr_pt": curr.p_top,
                            "drift_c": drift_critical,
                            "drift_t": drift_top,
                            "scored_at": now,
                        }
                    )
        
        if axes:
            drift_summary.append({
                "server_id": server.server_id,
                "server_name": server.name,
                "risk_tier": server.risk_tier or "unknown",
                "max_axis_drift": round(max_drift, 4),
                "axes": axes,
            })
    
    session.commit()
    
    drift_summary.sort(key=lambda x: x["max_axis_drift"], reverse=True)
    
    return {
        "days": days,
        "total_servers": total_servers,
        "drift_summary": drift_summary,
    }


def get_drift_summary(session: Session, days: int = 7) -> dict:
    """Get cached drift summary or compute fresh."""
    return compute_drift(session, days)


def health(session: Session) -> dict:
    """Health check."""
    return {"status": "ok"}


def trust_score_distribution(session: Session) -> dict:
    """Get trust score distribution."""
    servers = session.query(McpServerRegistry).all()
    distribution = {}
    for s in servers:
        tier = s.risk_tier or "unknown"
        distribution[tier] = distribution.get(tier, 0) + 1
    return distribution


def trusted_servers(session: Session, min_trust: float = 0.7) -> List[dict]:
    """Get trusted servers."""
    servers = session.query(McpServerRegistry).filter(
        McpServerRegistry.trust_score >= min_trust
    ).all()
    return [{"server_id": s.server_id, "name": s.name, "trust_score": s.trust_score} for s in servers]


def cycle(session: Session) -> dict:
    """Run drift cycle."""
    return compute_drift(session, days=7)


def compute_top_cves(session: Session, limit: int = 10) -> List[dict]:
    """Compute top CVEs (placeholder for compatibility)."""
    return []


def get_cves_for_cvss_range(session: Session, min_cvss: float, max_cvss: float) -> List[dict]:
    """Get CVEs for CVSS range (placeholder)."""
    return []


def run(session: Session) -> dict:
    """Run the service."""
    return compute_drift(session, days=7)


def get_threat_associations(session: Session) -> dict:
    """Get threat associations (placeholder)."""
    return {}


def record_family_history(session: Session, family_id: str) -> dict:
    """Record family history (placeholder)."""
    return {"family_id": family_id, "recorded": True}


def get_coverage_gaps(session: Session) -> List[dict]:
    """Get coverage gaps (placeholder)."""
    return []


def compute_fill_id(session: Session, entity_id: str) -> str:
    """Compute fill ID (placeholder)."""
    return f"fill_{entity_id}"


def get_definition_history(session: Session, entity_id: str) -> List[dict]:
    """Get definition history (placeholder)."""
    return []


def get_risk_tier_trend(session: Session, days: int = 30) -> dict:
    """Get risk tier trend (placeholder)."""
    return {}


def get_signal_count(session: Session) -> int:
    """Get signal count (placeholder)."""
    return 0


def persist_risk_tier(session: Session, server_id: str, tier: str) -> dict:
    """Persist risk tier (placeholder)."""
    return {"server_id": server_id, "tier": tier}


def score_servers(session: Session) -> List[dict]:
    """Score servers (placeholder)."""
    return []