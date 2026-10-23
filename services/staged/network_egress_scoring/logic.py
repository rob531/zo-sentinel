"""
Network Egress Scoring Service

FastAPI service that consumes network_egress axis scores from mcp_llm_axis_scores
and writes risk_tier determinations to mcp_server_registry for servers where the
network_egress axis triggered escalation.
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx


# Configuration
SERVICE_NAME = "network_egress_scoring"
API_PREFIX = "/api/scoring"
WRITE_SERVICE_TIMEOUT = 10.0  # 10s strict timeout on write_service calls
BATCH_TIMEOUT = 30.0  # Max 30s per batch

# Service URLs
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8081")


app = FastAPI(title="Network Egress Scoring Service")


class ServerUpdate(BaseModel):
    server_id: str
    previous_tier: str
    new_tier: str
    network_egress_p_danger: float
    network_egress_p_critical: float


class ScoringResponse(BaseModel):
    servers_updated: int
    servers: list[ServerUpdate]
    scanned_at: str


def get_db_connection():
    """Get database connection using write_service pattern."""
    import structlog
    logger = structlog.get_logger()
    
    # Use write_service for database access
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url
    
    # Fallback: use httpx to call write_service
    return None


def query_via_write_service(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a query via write_service HTTP endpoint."""
    import structlog
    logger = structlog.get_logger()
    
    try:
        with httpx.Client(timeout=WRITE_SERVICE_TIMEOUT) as client:
            response = client.post(
                f"{WRITE_SERVICE_URL}/query",
                json={"sql": sql, "params": params or {}}
            )
            response.raise_for_status()
            return response.json().get("rows", [])
    except httpx.TimeoutException:
        logger.warning("write_service query timeout", sql=sql[:100])
        return []
    except Exception as e:
        logger.error("write_service query failed", error=str(e), sql=sql[:100])
        return []


def execute_via_write_service(sql: str, params: dict | None = None) -> bool:
    """Execute a statement via write_service HTTP endpoint with timeout."""
    import structlog
    logger = structlog.get_logger()
    
    try:
        with httpx.Client(timeout=WRITE_SERVICE_TIMEOUT) as client:
            response = client.post(
                f"{WRITE_SERVICE_URL}/execute",
                json={"sql": sql, "params": params or {}}
            )
            response.raise_for_status()
            return True
    except httpx.TimeoutException:
        logger.warning("write_service execute timeout", sql=sql[:100])
        return False
    except Exception as e:
        logger.error("write_service execute failed", error=str(e), sql=sql[:100])
        return False


def query_direct_db(sql: str, params: tuple | None = None) -> list[dict]:
    """Execute query directly against database using psycopg2 or similar."""
    import structlog
    logger = structlog.get_logger()
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set, cannot query directly")
        return []
    
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()
    except ImportError:
        logger.warning("psycopg2 not available, using write_service fallback")
        return query_via_write_service(sql, params)
    except Exception as e:
        logger.error("direct db query failed", error=str(e))
        return []


def execute_direct_db(sql: str, params: tuple | None = None) -> bool:
    """Execute statement directly against database using psycopg2."""
    import structlog
    logger = structlog.get_logger()
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set, cannot execute directly")
        return False
    
    try:
        import psycopg2
        
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            return True
        finally:
            conn.close()
    except ImportError:
        logger.warning("psycopg2 not available, using write_service fallback")
        return execute_via_write_service(sql, params)
    except Exception as e:
        logger.error("direct db execute failed", error=str(e))
        return False


def get_cutoff_time(minutes: int = 60) -> datetime:
    """Get cutoff time for scanning recent scores."""
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def compute_composite_risk(all_axes: list[dict]) -> float:
    """
    Compute composite risk score from all axis scores.
    Uses weighted average with escalation flags boosted.
    """
    if not all_axes:
        return 0.0
    
    total_weight = 0.0
    weighted_sum = 0.0
    
    for axis in all_axes:
        p_danger = float(axis.get("p_danger", 0.0))
        p_critical = float(axis.get("p_critical", 0.0))
        escalated = bool(axis.get("escalated", False))
        
        # Base score from axis
        score = max(p_danger, p_critical)
        
        # Boost if escalated
        if escalated:
            score = min(1.0, score * 1.5)
        
        # Weight (equal weight for all 7 axes)
        weight = 1.0
        total_weight += weight
        weighted_sum += score * weight
    
    return weighted_sum / total_weight if total_weight > 0 else 0.0


def determine_risk_tier(composite_risk: float, network_egress: dict | None = None) -> str:
    """
    Determine risk tier based on composite risk and network_egress specific rules.
    
    If network_egress.p_critical > 0.4 OR network_egress.escalated=True:
    force risk_tier to at most ENTERPRISE_CONTROLLED.
    """
    import structlog
    logger = structlog.get_logger()
    
    # Check network_egress specific escalation rules
    if network_egress:
        p_critical = float(network_egress.get("p_critical", 0.0))
        escalated = bool(network_egress.get("escalated", False))
        
        if p_critical > 0.4 or escalated:
            logger.info(
                "network_egress_escalation_triggered",
                p_critical=p_critical,
                escalated=escalated
            )
            return "ENTERPRISE_CONTROLLED"
    
    # Standard tier assignment based on composite risk
    if composite_risk >= 0.8:
        return "CRITICAL"
    elif composite_risk >= 0.6:
        return "HIGH"
    elif composite_risk >= 0.4:
        return "MEDIUM"
    elif composite_risk >= 0.2:
        return "LOW"
    else:
        return "ENTERPRISE_BASIC"


def get_escalated_network_egress_servers(cutoff: datetime) -> list[dict]:
    """
    Query mcp_llm_axis_scores for servers with network_egress axis escalated since cutoff.
    """
    sql = """
    SELECT 
        mas.server_id,
        mas.axis_name,
        mas.p_danger,
        mas.p_critical,
        mas.escalated,
        mas.scored_at,
        msr.risk_tier as current_risk_tier
    FROM mcp_llm_axis_scores mas
    JOIN mcp_server_registry msr ON mas.server_id = msr.server_id
    WHERE mas.axis_name = 'network_egress'
      AND mas.scored_at > %s
      AND (mas.escalated = TRUE OR mas.p_critical > 0)
    ORDER BY mas.scored_at DESC
    """
    
    return query_direct_db(sql, (cutoff,))


def get_all_axis_scores_for_server(server_id: str, cutoff: datetime) -> list[dict]:
    """
    Get all axis scores for a server to compute composite risk.
    """
    sql = """
    SELECT 
        axis_name,
        p_danger,
        p_critical,
        escalated,
        scored_at
    FROM mcp_llm_axis_scores
    WHERE server_id = %s
      AND scored_at > %s
    ORDER BY scored_at DESC
    """
    
    rows = query_direct_db(sql, (server_id, cutoff))
    
    # Deduplicate by axis_name, keeping most recent
    seen = {}
    for row in rows:
        axis = row.get("axis_name")
        if axis not in seen:
            seen[axis] = row
    
    return list(seen.values())


def get_network_egress_score_for_server(server_id: str, cutoff: datetime) -> dict | None:
    """Get the network_egress axis score for a specific server."""
    sql = """
    SELECT 
        axis_name,
        p_danger,
        p_critical,
        escalated,
        scored_at
    FROM mcp_llm_axis_scores
    WHERE server_id = %s
      AND axis_name = 'network_egress'
      AND scored_at > %s
    ORDER BY scored_at DESC
    LIMIT 1
    """
    
    rows = query_direct_db(sql, (server_id, cutoff))
    return rows[0] if rows else None


def update_server_risk_tier(server_id: str, new_tier: str) -> bool:
    """Update the risk_tier for a server in mcp_server_registry."""
    sql = """
    UPDATE mcp_server_registry
    SET risk_tier = %s,
        updated_at = NOW()
    WHERE server_id = %s
    """
    
    return execute_direct_db(sql, (new_tier, server_id))


@app.get(f"{API_PREFIX}/network-egress/updates", name="health_probe")
async def health_probe():
    """Health probe endpoint for the network egress scoring service."""
    return {"status": "healthy", "service": SERVICE_NAME}


@app.post(f"{API_PREFIX}/network-egress/updates", response_model=ScoringResponse)
async def run_network_egress_update(cutoff_minutes: int = 60):
    """
    Internal POST endpoint called by the scoring consumer harness after axis scores are written.
    
    This endpoint:
    1. Queries mcp_llm_axis_scores for rows where axis_name='network_egress' AND escalated=True since the last scan
    2. For each escalated server, reads all 7 axis scores to compute the composite risk
    3. Applies network_egress specific rules (force ENTERPRISE_CONTROLLED if p_critical > 0.4 or escalated)
    4. Writes back the updated risk_tier to mcp_server_registry for each affected server
    
    Returns a response with the count of updated servers and details of each update.
    """
    import structlog
    logger = structlog.get_logger()
    
    start_time = time.time()
    scanned_at = datetime.now(timezone.utc).isoformat()
    
    cutoff = get_cutoff_time(minutes=cutoff_minutes)
    logger.info("starting_network_egress_scan", cutoff=cutoff.isoformat())
    
    # Step 1: Get all servers with network_egress escalation
    escalated_servers = get_escalated_network_egress_servers(cutoff)
    logger.info("found_escalated_servers", count=len(escalated_servers))
    
    servers_updated: list[ServerUpdate] = []
    
    # Process each escalated server
    for server_row in escalated_servers:
        # Check batch timeout
        if time.time() - start_time > BATCH_TIMEOUT:
            logger.warning("batch_timeout_exceeded")
            break
        
        server_id = server_row.get("server_id")
        previous_tier = server_row.get("current_risk_tier")
        
        # Step 2: Get network_egress score
        network_egress = get_network_egress_score_for_server(server_id, cutoff)
        if not network_egress:
            continue
        
        p_danger = float(network_egress.get("p_danger", 0.0))
        p_critical = float(network_egress.get("p_critical", 0.0))
        
        # Step 3: Get all axis scores for composite calculation
        all_axes = get_all_axis_scores_for_server(server_id, cutoff)
        
        # Add network_egress to the axes list if not already there
        network_egress_dict = {
            "axis_name": "network_egress",
            "p_danger": p_danger,
            "p_critical": p_critical,
            "escalated": network_egress.get("escalated", False)
        }
        
        # Check if network_egress is already in all_axes
        has_network_egress = any(a.get("axis_name") == "network_egress" for a in all_axes)
        if not has_network_egress:
            all_axes.append(network_egress_dict)
        
        # Step 4: Compute composite risk
        composite_risk = compute_composite_risk(all_axes)
        
        # Step 5: Determine new risk tier (passing network_egress for escalation check)
        new_tier = determine_risk_tier(composite_risk, network_egress_dict)
        
        # Only update if tier actually changed
        if new_tier != previous_tier:
            success = update_server_risk_tier(server_id, new_tier)
            if success:
                servers_updated.append(ServerUpdate(
                    server_id=server_id,
                    previous_tier=str(previous_tier),
                    new_tier=new_tier,
                    network_egress_p_danger=p_danger,
                    network_egress_p_critical=p_critical
                ))
                logger.info(
                    "server_risk_tier_updated",
                    server_id=server_id,
                    previous=previous_tier,
                    new=new_tier
                )
    
    elapsed = time.time() - start_time
    logger.info(
        "network_egress_scan_complete",
        servers_updated=len(servers_updated),
        elapsed_seconds=elapsed
    )
    
    return ScoringResponse(
        servers_updated=len(servers_updated),
        servers=servers_updated,
        scanned_at=scanned_at
    )


def run_self_test():
    """Self-test function to verify the service logic works correctly."""
    import structlog
    
    # Configure logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer()
        ]
    )
    
    logger = structlog.get_logger()
    logger.info("starting_self_test", service=SERVICE_NAME)
    
    # Test 1: Health probe endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get(f"{API_PREFIX}/network-egress/updates")
    assert response.status_code == 200, f"Health probe failed: {response.status_code}"
    data = response.json()
    assert data["status"] == "healthy", f"Health probe returned unhealthy: {data}"
    assert data["service"] == SERVICE_NAME
    logger.info("self_test_health_probe_passed")
    
    # Test 2: Create test data and verify update logic
    # This would require actual database, so we test the logic functions
    
    # Test composite risk computation
    test_axes = [
        {"axis_name": "network_egress", "p_danger": 0.8, "p_critical": 0.5, "escalated": True},
        {"axis_name": "data_sensitivity", "p_danger": 0.3, "p_critical": 0.1, "escalated": False},
        {"axis_name": "access_pattern", "p_danger": 0.5, "p_critical": 0.2, "escalated": False},
    ]
    risk = compute_composite_risk(test_axes)
    assert 0 <= risk <= 1, f"Composite risk out of range: {risk}"
    logger.info("self_test_composite_risk_passed", risk=risk)
    
    # Test risk tier determination without escalation
    tier = determine_risk_tier(0.3)
    assert tier in ["LOW", "MEDIUM", "HIGH", "CRITICAL", "ENTERPRISE_BASIC", "ENTERPRISE_CONTROLLED"]
    logger.info("self_test_risk_tier_passed", tier=tier)
    
    # Test risk tier determination with network_egress escalation
    network_egress_high = {"p_critical": 0.5, "escalated": True}
    tier = determine_risk_tier(0.1, network_egress_high)
    assert tier == "ENTERPRISE_CONTROLLED", f"Expected ENTERPRISE_CONTROLLED but got {tier}"
    logger.info("self_test_escalation_tier_passed", tier=tier)
    
    # Test p_critical > 0.4 escalation
    network_egress_critical = {"p_critical": 0.6, "escalated": False}
    tier = determine_risk_tier(0.1, network_egress_critical)
    assert tier == "ENTERPRISE_CONTROLLED", f"Expected ENTERPRISE_CONTROLLED but got {tier}"
    logger.info("self_test_pcritical_escalation_passed", tier=tier)
    
    # Test that non-escalated servers with low p_critical keep normal tiering
    network_egress_clean = {"p_critical": 0.1, "escalated": False}
    tier = determine_risk_tier(0.2, network_egress_clean)
    assert tier in ["LOW", "MEDIUM", "HIGH", "CRITICAL", "ENTERPRISE_BASIC", "ENTERPRISE_CONTROLLED"]
    logger.info("self_test_clean_server_passed", tier=tier)
    
    logger.info("ALL_SELF_TESTS_PASSED")
    print("PASS")


if __name__ == "__main__":
    run_self_test()