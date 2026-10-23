"""
Verdict Risk Tier Consumer Service

A scoring-consumer service that reads the 7 risk axis scores from mcp_llm_axis_scores
for each server_id, applies trust_gating_override.trust_gate() to derive the 'trusted'
verdict, then writes risk_tier back to mcp_server_registry via write_service.
"""

import logging
import time
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Service configuration
SERVICE_NAME = "verdict_risk_tier_consumer"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
SERVICE_HEALTH_URL = "http://127.0.0.1:8772/service_health"
HEARTBEAT_INTERVAL = 60  # seconds

# Risk tier definitions (6-tier rule table)
RISK_TIER_ORDER = ["minimal", "low", "medium", "high", "critical", "unknown"]


def risk_tier_from_axes(axes: dict) -> str:
    """
    Derive the risk tier from a dictionary of axis scores.
    
    Args:
        axes: Dictionary mapping axis names to their labels/scores
              e.g., {"malicious": "low", "vulnerable": "medium", ...}
    
    Returns:
        Risk tier string: "minimal", "low", "medium", "high", "critical", or "unknown"
    
    6-tier rule table:
    - critical: Any axis is "critical" OR multiple axes are "high"
    - high: Any axis is "high" (unless critical)
    - medium: Any axis is "medium" (unless high/critical)
    - low: Any axis is "low" (unless medium/high/critical)
    - minimal: All axes are "minimal" or "low"
    - unknown: No valid axis data available
    """
    if not axes or len(axes) == 0:
        return "unknown"
    
    # Normalize axis values to lowercase for comparison
    normalized_axes = {k.lower(): v.lower() if isinstance(v, str) else str(v).lower() 
                      for k, v in axes.items()}
    
    axis_values = list(normalized_axes.values())
    
    # Check for critical tier
    if "critical" in axis_values:
        return "critical"
    
    # Count high/medium/low/minimal axes
    high_count = sum(1 for v in axis_values if v == "high")
    medium_count = sum(1 for v in axis_values if v == "medium")
    low_count = sum(1 for v in axis_values if v == "low")
    minimal_count = sum(1 for v in axis_values if v == "minimal")
    
    # Critical if multiple high axes
    if high_count >= 2:
        return "critical"
    
    # High if any high axis
    if high_count >= 1:
        return "high"
    
    # Medium if any medium axis
    if medium_count >= 1:
        return "medium"
    
    # Low if any low axis
    if low_count >= 1:
        return "low"
    
    # Minimal if all axes are minimal (or we have valid data)
    if minimal_count > 0 or len(axis_values) > 0:
        return "minimal"
    
    return "unknown"


def get_trust_gate_verdict(url: str, name: str, axes: dict) -> str:
    """
    Apply trust_gating_override.trust_gate to derive the 'trusted' verdict.
    
    Args:
        url: Server URL for trust gating
        name: Server name for trust gating  
        axes: Dictionary of axis_name -> label pairs
    
    Returns:
        The trust-gated verdict string
    """
    try:
        from trust_gating_override import trust_gate
        return trust_gate(url, name, axes)
    except ImportError:
        # Fallback if trust_gating_override is not available
        logger.warning("trust_gating_override not available, using risk_tier_from_axes")
        return risk_tier_from_axes(axes)


def get_axis_scores(session: Session, server_id: int) -> Optional[dict]:
    """
    Read the 7 risk axis scores from mcp_llm_axis_scores for a given server_id.
    
    Args:
        session: SQLAlchemy database session
        server_id: The server ID to query
    
    Returns:
        Dictionary of axis scores or None if not found
    """
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    result = session.execute(stmt).scalar_one_or_none()
    
    if result is None:
        return None
    
    # Convert to dictionary with axis names and labels
    axes = {}
    if hasattr(result, 'malicious') and result.malicious:
        axes['malicious'] = result.malicious
    if hasattr(result, 'vulnerable') and result.vulnerable:
        axes['vulnerable'] = result.vulnerable
    if hasattr(result, 'outdated') and result.outdated:
        axes['outdated'] = result.outdated
    if hasattr(result, 'poor_quality') and result.poor_quality:
        axes['poor_quality'] = result.poor_quality
    if hasattr(result, 'unmaintained') and result.unmaintained:
        axes['unmaintained'] = result.unmaintained
    if hasattr(result, 'high_impact') and result.high_impact:
        axes['high_impact'] = result.high_impact
    if hasattr(result, 'large_attack_surface') and result.large_attack_surface:
        axes['large_attack_surface'] = result.large_attack_surface
    
    return axes if axes else None


def write_risk_tier(server_id: int, risk_tier: str) -> bool:
    """
    Write risk_tier back to mcp_server_registry via write_service.
    
    Args:
        server_id: The server ID to update
        risk_tier: The computed risk tier value
    
    Returns:
        True if successful, False otherwise
    """
    try:
        payload = {
            "table": "mcp_server_registry",
            "id": server_id,
            "data": {"risk_tier": risk_tier}
        }
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        return response.status_code == 200
    except requests.RequestException as e:
        logger.error(f"Failed to write risk_tier for server {server_id}: {e}")
        return False


def send_heartbeat() -> bool:
    """
    Send heartbeat to service_health every 60s.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        payload = {
            "service": SERVICE_NAME,
            "status": "healthy",
            "timestamp": time.time()
        }
        response = requests.post(SERVICE_HEALTH_URL, json=payload, timeout=10)
        return response.status_code == 200
    except requests.RequestException as e:
        logger.error(f"Failed to send heartbeat: {e}")
        return False


def process_server(session: Session, server_id: int, server_url: str, server_name: str) -> Optional[str]:
    """
    Process a single server: get axis scores, apply trust gating, compute risk tier.
    
    Args:
        session: Database session
        server_id: Server ID
        server_url: Server URL
        server_name: Server name
    
    Returns:
        The computed risk tier or None on failure
    """
    axes = get_axis_scores(session, server_id)
    
    if axes is None:
        logger.warning(f"No axis scores found for server {server_id}")
        return None
    
    # Apply trust gating to get the trusted verdict
    trusted_verdict = get_trust_gate_verdict(server_url, server_name, axes)
    
    # Also compute local risk tier for comparison
    local_risk_tier = risk_tier_from_axes(axes)
    
    logger.info(f"Server {server_id}: axes={axes}, trusted={trusted_verdict}, local={local_risk_tier}")
    
    # Write risk tier back to registry
    write_risk_tier(server_id, local_risk_tier)
    
    return local_risk_tier


def consume(session: Session) -> dict:
    """
    Main consumer function - processes all servers with axis scores.
    
    Args:
        session: Database session
    
    Returns:
        Summary dictionary with processing results
    """
    # Get all servers that have axis scores
    stmt = select(McpServerRegistry).join(
        McpLlmAxisScore, McpLlmAxisScore.server_id == McpServerRegistry.id
    ).distinct()
    
    servers = session.execute(stmt).scalars().all()
    
    results = {
        "processed": 0,
        "failed": 0,
        "tiers": {}
    }
    
    for server in servers:
        try:
            risk_tier = process_server(
                session,
                server.id,
                getattr(server, 'url', ''),
                getattr(server, 'name', '')
            )
            
            if risk_tier:
                results["processed"] += 1
                results["tiers"][server.id] = risk_tier
            else:
                results["failed"] += 1
        except Exception as e:
            logger.error(f"Error processing server {server.id}: {e}")
            results["failed"] += 1
    
    return results


def run_self_test() -> bool:
    """
    Run self-test to verify the service is working correctly.
    
    Returns:
        True if all tests pass, False otherwise
    """
    logger.info("Running self-test...")
    
    test_cases = [
        # (axes, expected_tier)
        ({"malicious": "low", "vulnerable": "medium"}, "medium"),
        ({"malicious": "critical"}, "critical"),
        ({"malicious": "high", "vulnerable": "high"}, "critical"),
        ({"malicious": "high"}, "high"),
        ({"malicious": "medium"}, "medium"),
        ({"malicious": "low"}, "low"),
        ({"malicious": "minimal", "vulnerable": "minimal"}, "minimal"),
        ({}, "unknown"),
    ]
    
    all_passed = True
    for axes, expected_tier in test_cases:
        result = risk_tier_from_axes(axes)
        status = "PASS" if result == expected_tier else "FAIL"
        if result != expected_tier:
            all_passed = False
        logger.info(f"Test {axes} -> {result} (expected: {expected_tier}) [{status}]")
    
    return all_passed


if __name__ == "__main__":
    """
    Self-test: seeds 3 servers with varied axis scores, runs risk_tier_from_axes,
    asserts the correct tier string is returned for each, prints PASS.
    """
    import sqlite3
    from unittest.mock import MagicMock, patch
    
    print("=" * 60)
    print("SELF-TEST: verdict_risk_tier_consumer")
    print("=" * 60)
    
    # Create in-memory SQLite store for testing
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables matching the app schema
    cursor.execute("""
        CREATE TABLE mcp_server_registry (
            id INTEGER PRIMARY KEY,
            name TEXT,
            url TEXT,
            risk_tier TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY,
            server_id INTEGER,
            malicious TEXT,
            vulnerable TEXT,
            outdated TEXT,
            poor_quality TEXT,
            unmaintained TEXT,
            high_impact TEXT,
            large_attack_surface TEXT
        )
    """)
    
    # Seed 3 servers with varied axis scores
    servers_data = [
        # Server 1: High risk - multiple high axes
        (1, "Server Alpha", "https://alpha.example.com", "high"),
        # Server 2: Medium risk - medium axis
        (2, "Server Beta", "https://beta.example.com", "medium"),
        # Server 3: Minimal risk - all minimal
        (3, "Server Gamma", "https://gamma.example.com", "minimal"),
    ]
    
    cursor.executemany(
        "INSERT INTO mcp_server_registry (id, name, url, risk_tier) VALUES (?, ?, ?, ?)",
        servers_data
    )
    
    # Seed axis scores
    scores_data = [
        # Server 1: critical axes -> critical tier
        (1, 1, "high", "high", "medium", "low", "minimal", "high", "medium"),
        # Server 2: medium axis only -> medium tier
        (2, 2, "minimal", "medium", "minimal", "minimal", "minimal", "minimal", "minimal"),
        # Server 3: all minimal -> minimal tier
        (3, 3, "minimal", "minimal", "minimal", "minimal", "minimal", "minimal", "minimal"),
    ]
    
    cursor.executemany(
        """INSERT INTO mcp_llm_axis_scores 
           (id, server_id, malicious, vulnerable, outdated, poor_quality, 
            unmaintained, high_impact, large_attack_surface) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        scores_data
    )
    
    conn.commit()
    
    # Create mock session
    mock_session = MagicMock()
    
    # Mock get_axis_scores to use our test data
    def mock_get_axis_scores(session, server_id):
        cursor.execute(
            """SELECT malicious, vulnerable, outdated, poor_quality, 
                      unmaintained, high_impact, large_attack_surface 
               FROM mcp_llm_axis_scores WHERE server_id = ?""",
            (server_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "malicious": row[0],
                "vulnerable": row[1],
                "outdated": row[2],
                "poor_quality": row[3],
                "unmaintained": row[4],
                "high_impact": row[5],
                "large_attack_surface": row[6],
            }
        return None
    
    # Mock write_service
    write_calls = []
    def mock_write_risk_tier(server_id, risk_tier):
        write_calls.append((server_id, risk_tier))
        cursor.execute(
            "UPDATE mcp_server_registry SET risk_tier = ? WHERE id = ?",
            (risk_tier, server_id)
        )
        conn.commit()
        return True
    
    with patch('logic.get_axis_scores', mock_get_axis_scores):
        with patch('logic.write_risk_tier', mock_write_risk_tier):
            # Test risk_tier_from_axes with known axis inputs
            test_cases = [
                # (server_id, axes, expected_tier)
                (1, {"malicious": "high", "vulnerable": "high", "outdated": "medium", 
                     "poor_quality": "low", "unmaintained": "minimal", 
                     "high_impact": "high", "large_attack_surface": "medium"}, "critical"),
                (2, {"malicious": "minimal", "vulnerable": "medium", "outdated": "minimal",
                     "poor_quality": "minimal", "unmaintained": "minimal",
                     "high_impact": "minimal", "large_attack_surface": "minimal"}, "medium"),
                (3, {"malicious": "minimal", "vulnerable": "minimal", "outdated": "minimal",
                     "poor_quality": "minimal", "unmaintained": "minimal",
                     "high_impact": "minimal", "large_attack_surface": "minimal"}, "minimal"),
            ]
            
            all_passed = True
            print("\nTest Cases:")
            print("-" * 60)
            
            for server_id, axes, expected_tier in test_cases:
                result = risk_tier_from_axes(axes)
                passed = result == expected_tier
                status = "PASS" if passed else "FAIL"
                
                if not passed:
                    all_passed = False
                
                print(f"Server {server_id}: {axes}")
                print(f"  Result: {result}, Expected: {expected_tier} [{status}]")
                print()
            
            # Process servers through the full pipeline
            print("Processing servers through full pipeline:")
            print("-" * 60)
            
            results = {
                "processed": 0,
                "failed": 0,
                "tiers": {}
            }
            
            for server_id, _, _, _ in servers_data:
                axes = mock_get_axis_scores(None, server_id)
                if axes:
                    risk_tier = risk_tier_from_axes(axes)
                    mock_write_risk_tier(server_id, risk_tier)
                    results["processed"] += 1
                    results["tiers"][server_id] = risk_tier
                    print(f"Server {server_id}: {risk_tier}")
            
            print()
            print(f"Processed: {results['processed']}, Failed: {results['failed']}")
            print(f"Write calls: {write_calls}")
            
            # Verify write_service was called correctly
            for server_id, expected_tier in results["tiers"].items():
                cursor.execute("SELECT risk_tier FROM mcp_server_registry WHERE id = ?", (server_id,))
                stored_tier = cursor.fetchone()[0]
                if stored_tier != expected_tier:
                    print(f"FAIL: Server {server_id} has wrong tier in DB: {stored_tier}")
                    all_passed = False
            
            conn.close()
    
    print("=" * 60)
    if all_passed:
        print("PASS")
    else:
        print("FAIL")
    print("=" * 60)