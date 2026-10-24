"""
axis_stability_scoring_consumer/logic.py

Scoring consumer that detects axis label instability across recent scoring waves
for each server. Reads mcp_llm_axis_scores + mcp_server_registry via write_service query.
"""

import json
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

# Service configuration
WRITE_SERVICE_URL = "http://localhost:8081"

# Query templates for write_service
SERVER_REGISTRY_QUERY = """
SELECT server_id, risk_tier
FROM mcp_server_registry
ORDER BY server_id
"""

AXIS_SCORES_QUERY = """
SELECT server_id, axis_name, label, scored_at, label_index
FROM mcp_llm_axis_scores
WHERE server_id = %s
ORDER BY axis_name, scored_at DESC
"""

EMIT_SCORING_QUERY = """
INSERT INTO scoring_consumer (server_id, axis_count, unstable_axes, instability_score, computed_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (server_id) DO UPDATE SET
    axis_count = EXCLUDED.axis_count,
    unstable_axes = EXCLUDED.unstable_axes,
    instability_score = EXCLUDED.instability_score,
    computed_at = EXCLUDED.computed_at
"""


def query_write_service(query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """
    Execute a query against the write_service API.
    
    Args:
        query: SQL query string
        params: Optional query parameters
        
    Returns:
        List of result rows as dictionaries
    """
    payload = {"query": query}
    if params:
        payload["params"] = params
    
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json().get("rows", [])
    except requests.exceptions.RequestException as e:
        print(f"Warning: write_service query failed: {e}", file=sys.stderr)
        return []


def emit_scoring(server_id: str, axis_count: int, unstable_axes: List[str], 
                 instability_score: float) -> bool:
    """
    Emit scoring result to the scoring-consumer table.
    
    Args:
        server_id: Server identifier
        axis_count: Total number of axes evaluated
        unstable_axes: List of axes with instability
        instability_score: Calculated instability score (0-100)
        
    Returns:
        True if emission succeeded, False otherwise
    """
    query = EMIT_SCORING_QUERY
    params = (
        server_id,
        axis_count,
        json.dumps(unstable_axes),
        instability_score,
        datetime.utcnow().isoformat()
    )
    
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"query": query, "params": params},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Warning: Failed to emit scoring for {server_id}: {e}", file=sys.stderr)
        return False


def compute_stability(server_id: str, 
                      score_data: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """
    Compute axis label instability for a server.
    
    Compares the two most recent scored_at timestamps per axis. Flags axes
    where the label changed between consecutive scoring waves.
    
    Args:
        server_id: Server identifier to analyze
        score_data: Optional dict mapping server_id -> list of score rows
                   (for testing with synthetic data)
                   
    Returns:
        Dict with:
        - server_id: The analyzed server
        - instability_flags: List of axes where label changed
        - axis_count: Total unique axes found
        - instability_score: 0.0 (stable) to 100.0 (all changed)
    """
    if score_data is not None:
        # Use provided test data (in-memory)
        rows = score_data.get(server_id, [])
    else:
        # Query real data via write_service
        rows = query_write_service(AXIS_SCORES_QUERY, (server_id,))
    
    # Group by axis_name
    axis_groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        axis_name = row.get("axis_name")
        if axis_name not in axis_groups:
            axis_groups[axis_name] = []
        axis_groups[axis_name].append(row)
    
    # Sort each axis group by scored_at DESC and take top 2
    instability_flags: List[str] = []
    total_axes = len(axis_groups)
    
    for axis_name, axis_rows in axis_groups.items():
        # Sort by scored_at descending (most recent first)
        sorted_rows = sorted(
            axis_rows, 
            key=lambda r: r.get("scored_at", ""), 
            reverse=True
        )
        
        # Take top 2 for comparison
        if len(sorted_rows) >= 2:
            latest_label = sorted_rows[0].get("label")
            previous_label = sorted_rows[1].get("label")
            
            # Flag instability if labels differ
            if latest_label != previous_label:
                instability_flags.append(axis_name)
    
    # Calculate instability score
    if total_axes > 0:
        instability_score = (len(instability_flags) / total_axes) * 100.0
    else:
        instability_score = 0.0
    
    return {
        "server_id": server_id,
        "axis_count": total_axes,
        "unstable_axes": instability_flags,
        "instability_score": instability_score
    }


def run() -> List[Dict[str, Any]]:
    """
    Main entry point: iterate all servers, compute stability, emit results.
    
    Reads server registry, computes instability for each server,
    and emits scoring to the scoring-consumer table.
    
    Returns:
        List of computed stability results for all servers
    """
    results: List[Dict[str, Any]] = []
    
    # Get all servers from registry
    servers = query_write_service(SERVER_REGISTRY_QUERY)
    
    if not servers:
        print("Warning: No servers found in registry", file=sys.stderr)
        return results
    
    for server_row in servers:
        server_id = server_row.get("server_id")
        if not server_id:
            continue
        
        # Compute stability for this server
        stability_result = compute_stability(server_id)
        
        # Emit to scoring-consumer table
        emit_scoring(
            server_id=stability_result["server_id"],
            axis_count=stability_result["axis_count"],
            unstable_axes=stability_result["unstable_axes"],
            instability_score=stability_result["instability_score"]
        )
        
        results.append(stability_result)
    
    return results


def _run_self_test() -> bool:
    """
    Self-test: validate compute_stability with synthetic in-memory data.
    
    Seeds 3 synthetic scored_at timestamps for 2 axes, verifies:
    - Stable axis (same label) returns 0.0 instability_score
    - Changed axis returns 50.0 instability_score (1 of 2 changed)
    """
    # Base timestamp
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    
    # Synthetic data simulating write_service query results
    # Server "test-server-001" has 2 axes with 3 scoring waves each
    synthetic_data = {
        "test-server-001": [
            # Axis "accuracy" - all stable (same label across waves)
            {"axis_name": "accuracy", "label": "precise", "scored_at": (base_time + timedelta(hours=0)).isoformat(), "label_index": 0},
            {"axis_name": "accuracy", "label": "precise", "scored_at": (base_time + timedelta(hours=1)).isoformat(), "label_index": 0},
            {"axis_name": "accuracy", "label": "precise", "scored_at": (base_time + timedelta(hours=2)).isoformat(), "label_index": 0},
            
            # Axis "helpfulness" - changed from "helpful" to "neutral" to "helpful"
            # Compare top 2 (most recent): should differ -> flag instability
            {"axis_name": "helpfulness", "label": "helpful", "scored_at": (base_time + timedelta(hours=0)).isoformat(), "label_index": 1},
            {"axis_name": "helpfulness", "label": "neutral", "scored_at": (base_time + timedelta(hours=1)).isoformat(), "label_index": 1},
            {"axis_name": "helpfulness", "label": "helpful", "scored_at": (base_time + timedelta(hours=2)).isoformat(), "label_index": 1},
        ],
        "test-server-002": [
            # Server with all stable axes
            {"axis_name": "accuracy", "label": "precise", "scored_at": (base_time + timedelta(hours=0)).isoformat(), "label_index": 0},
            {"axis_name": "accuracy", "label": "precise", "scored_at": (base_time + timedelta(hours=1)).isoformat(), "label_index": 0},
            {"axis_name": "helpfulness", "label": "helpful", "scored_at": (base_time + timedelta(hours=0)).isoformat(), "label_index": 1},
            {"axis_name": "helpfulness", "label": "helpful", "scored_at": (base_time + timedelta(hours=1)).isoformat(), "label_index": 1},
        ]
    }
    
    # Test server with one unstable axis
    result1 = compute_stability("test-server-001", synthetic_data)
    assert result1["axis_count"] == 2, f"Expected 2 axes, got {result1['axis_count']}"
    assert "helpfulness" in result1["unstable_axes"], f"Expected 'helpfulness' in unstable_axes, got {result1['unstable_axes']}"
    assert "accuracy" not in result1["unstable_axes"], f"Expected 'accuracy' NOT in unstable_axes"
    assert result1["instability_score"] == 50.0, f"Expected 50.0 instability_score, got {result1['instability_score']}"
    
    # Test server with all stable axes
    result2 = compute_stability("test-server-002", synthetic_data)
    assert result2["axis_count"] == 2, f"Expected 2 axes, got {result2['axis_count']}"
    assert len(result2["unstable_axes"]) == 0, f"Expected no unstable axes, got {result2['unstable_axes']}"
    assert result2["instability_score"] == 0.0, f"Expected 0.0 instability_score, got {result2['instability_score']}"
    
    # Test server with no data
    result3 = compute_stability("nonexistent-server", synthetic_data)
    assert result3["axis_count"] == 0, f"Expected 0 axes for nonexistent server, got {result3['axis_count']}"
    assert result3["instability_score"] == 0.0, f"Expected 0.0 instability_score, got {result3['instability_score']}"
    
    print("Self-test results:")
    print(f"  test-server-001: axis_count={result1['axis_count']}, unstable_axes={result1['unstable_axes']}, instability_score={result1['instability_score']}")
    print(f"  test-server-002: axis_count={result2['axis_count']}, unstable_axes={result2['unstable_axes']}, instability_score={result2['instability_score']}")
    print(f"  nonexistent: axis_count={result3['axis_count']}, instability_score={result3['instability_score']}")
    
    return True


if __name__ == "__main__":
    success = _run_self_test()
    if success:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)