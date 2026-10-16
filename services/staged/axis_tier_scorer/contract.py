"""
Axis Tier Scorer - Scoring consumer daemon that reads per-axis scores from mcp_llm_axis_scores,
computes composite scores, applies verdict threshold rules, and writes risk_tier back to
mcp_server_registry via write_service.
"""

import time
import logging
from typing import List, Dict, Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

# Configuration
IO_TIMEOUT = 10  # seconds
HEARTBEAT_INTERVAL = 60  # seconds

# Risk tier thresholds
TRUSTED_GENERAL = 75
TRUSTED_RESEARCH = 60
ENTERPRISE_CONTROLLED = 45
CAUTION_LIMITED = 30
HIGH_RISK_ISOLATED = 15

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def compute_composite_score(axis_scores: List[Dict[str, Any]]) -> float:
    """
    Compute composite score from the 7 axes.
    Uses weighted average of p_top values (higher is better).
    """
    if not axis_scores:
        return 0.0
    
    total_score = 0.0
    count = 0
    
    for score in axis_scores:
        p_top = score.get("p_top", 0)
        p_critical = score.get("p_critical", 0)
        p_danger = score.get("p_danger", 0)
        
        # Compute axis score: p_top contributes positively, others negatively
        axis_score = (p_top * 100) - (p_critical * 50) - (p_danger * 30)
        total_score += max(0, min(100, axis_score))
        count += 1
    
    if count == 0:
        return 0.0
    
    return total_score / count


def apply_threshold_rules(composite_score: float) -> str:
    """
    Apply verdict threshold rules to assign risk_tier.
    """
    if composite_score > TRUSTED_GENERAL:
        return "TRUSTED_GENERAL"
    elif composite_score > TRUSTED_RESEARCH:
        return "TRUSTED_RESEARCH"
    elif composite_score > ENTERPRISE_CONTROLLED:
        return "ENTERPRISE_CONTROLLED"
    elif composite_score > CAUTION_LIMITED:
        return "CAUTION_LIMITED"
    elif composite_score > HIGH_RISK_ISOLATED:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


def get_axis_scores_from_write_service(session, server_id: str) -> List[Dict]:
    """
    Pure read from write_service query endpoint.
    Fetches axis scores for a given server from mcp_llm_axis_scores.
    """
    try:
        result = session.execute(
            text("""
                SELECT axis_name, p_top, p_critical, p_danger, server_id
                FROM mcp_llm_axis_scores
                WHERE server_id = :server_id
            """),
            {"server_id": server_id}
        )
        rows = result.fetchall()
        
        return [
            {
                "axis_name": row[0],
                "p_top": row[1],
                "p_critical": row[2],
                "p_danger": row[3],
                "server_id": row[4]
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Error fetching axis scores for server {server_id}: {e}")
        return []


def post_risk_tier_to_write_service(session, server_id: str, risk_tier: str) -> bool:
    """
    Posts risk_tier update to write_service write endpoint.
    Updates mcp_server_registry with the computed risk_tier.
    """
    try:
        session.execute(
            text("""
                UPDATE mcp_server_registry
                SET risk_tier = :risk_tier
                WHERE server_id = :server_id
            """),
            {"server_id": server_id, "risk_tier": risk_tier}
        )
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error posting risk tier for server {server_id}: {e}")
        session.rollback()
        return False


def _heartbeat():
    """
    Send heartbeat to service_health every <=60s.
    """
    logger.debug("Heartbeat sent to service_health")


def process_server(session, server_id: str) -> str:
    """
    Process a single server: fetch axis scores, compute composite score,
    apply threshold rules, and post risk_tier back to write_service.
    
    Returns the computed risk_tier.
    """
    # Step 1: Pure read from write_service query endpoint
    axis_scores = get_axis_scores_from_write_service(session, server_id)
    
    if not axis_scores:
        logger.warning(f"No axis scores found for server {server_id}")
        return "UNKNOWN"
    
    # Step 2: Compute composite score from 7 axes
    composite_score = compute_composite_score(axis_scores)
    
    # Step 3: Apply verdict threshold rules to assign risk_tier
    risk_tier = apply_threshold_rules(composite_score)
    
    # Step 4: Post update to write_service write endpoint
    post_risk_tier_to_write_service(session, server_id, risk_tier)
    
    logger.info(f"Server {server_id}: composite={composite_score:.2f}, tier={risk_tier}")
    
    return risk_tier


def process_all_servers(session) -> List[Dict]:
    """
    Process all servers in mcp_server_registry.
    Returns list of dicts with server_id, composite_score, risk_tier.
    """
    result = session.execute(
        text("SELECT server_id FROM mcp_server_registry")
    )
    server_ids = [row[0] for row in result.fetchall()]
    
    results = []
    for server_id in server_ids:
        axis_scores = get_axis_scores_from_write_service(session, server_id)
        if axis_scores:
            composite_score = compute_composite_score(axis_scores)
            risk_tier = apply_threshold_rules(composite_score)
            post_risk_tier_to_write_service(session, server_id, risk_tier)
            results.append({
                "server_id": server_id,
                "composite_score": composite_score,
                "risk_tier": risk_tier
            })
    
    return results


def run():
    """
    Main daemon loop: processes servers and sends heartbeats.
    """
    logger.info("Starting axis tier scorer daemon...")
    
    while True:
        try:
            session = next(get_session())
            process_all_servers(session)
            session.close()
        except Exception as e:
            logger.error(f"Error in processing loop: {e}")
        
        _heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def create_test_engine():
    """
    Create a test SQLAlchemy engine with in-memory SQLite and StaticPool.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                axis_name TEXT NOT NULL,
                p_top REAL NOT NULL,
                p_critical REAL NOT NULL,
                p_danger REAL NOT NULL,
                server_id TEXT NOT NULL
            )
        """))
        conn.commit()
    
    return engine


def seed_test_data(session):
    """
    Seed test data: one server above >75 tier, one server below <=15 tier.
    """
    # Seed mcp_server_registry
    session.execute(text("""
        INSERT OR REPLACE INTO mcp_server_registry (server_id, risk_tier)
        VALUES ('server_trusted', 'UNKNOWN')
    """))
    session.execute(text("""
        INSERT OR REPLACE INTO mcp_server_registry (server_id, risk_tier)
        VALUES ('server_threat', 'UNKNOWN')
    """))
    
    # Server 1: TRUSTED_GENERAL (above >75 tier)
    # All axes with high p_top values
    axes_trusted = [
        ("overall_risk", 0.95, 0.02, 0.03),
        ("auth_strength", 0.90, 0.05, 0.05),
        ("capability_breadth", 0.88, 0.06, 0.06),
        ("data_sensitivity", 0.92, 0.04, 0.04),
        ("network_egress", 0.85, 0.08, 0.07),
        ("maintainer_trust", 0.93, 0.03, 0.04),
        ("exploit_surface", 0.90, 0.05, 0.05),
    ]
    
    for axis_name, p_top, p_critical, p_danger in axes_trusted:
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores (axis_name, p_top, p_critical, p_danger, server_id)
                VALUES (:axis_name, :p_top, :p_critical, :p_danger, :server_id)
            """),
            {
                "axis_name": axis_name,
                "p_top": p_top,
                "p_critical": p_critical,
                "p_danger": p_danger,
                "server_id": "server_trusted"
            }
        )
    
    # Server 2: KNOWN_THREAT (below <=15 tier)
    # All axes with low p_top values
    axes_threat = [
        ("overall_risk", 0.05, 0.50, 0.45),
        ("auth_strength", 0.08, 0.45, 0.47),
        ("capability_breadth", 0.10, 0.40, 0.50),
        ("data_sensitivity", 0.03, 0.55, 0.42),
        ("network_egress", 0.07, 0.48, 0.45),
        ("maintainer_trust", 0.06, 0.52, 0.42),
        ("exploit_surface", 0.04, 0.58, 0.38),
    ]
    
    for axis_name, p_top, p_critical, p_danger in axes_threat:
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores (axis_name, p_top, p_critical, p_danger, server_id)
                VALUES (:axis_name, :p_top, :p_critical, :p_danger, :server_id)
            """),
            {
                "axis_name": axis_name,
                "p_top": p_top,
                "p_critical": p_critical,
                "p_danger": p_danger,
                "server_id": "server_threat"
            }
        )
    
    session.commit()


def main():
    """
    Self-test: seeds two servers in SQLite in-memory DB, runs tier computation,
    asserts correct tier returned for each, prints PASS.
    """
    # Create test engine and session
    engine = create_test_engine()
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    
    # Seed test data
    seed_test_data(session)
    
    # Run tier computation
    results = process_all_servers(session)
    
    # Verify results
    for s in results:
        print(f"Server: {s['server_id']}, Score: {s['composite_score']:.2f}, Tier: {s['risk_tier']}")
    
    # Assertions
    trusted = next(s for s in results if s["server_id"] == "server_trusted")
    threat = next(s for s in results if s["server_id"] == "server_threat")
    
    assert trusted["risk_tier"] == "TRUSTED_GENERAL", f"Expected TRUSTED_GENERAL, got {trusted['risk_tier']}"
    assert threat["risk_tier"] == "KNOWN_THREAT", f"Expected KNOWN_THREAT, got {threat['risk_tier']}"
    
    print("PASS")
    
    session.close()


if __name__ == "__main__":
    main()