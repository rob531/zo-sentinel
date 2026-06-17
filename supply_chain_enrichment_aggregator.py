#!/usr/bin/env python3
"""
supply_chain_enrichment_aggregator.py

Pure enrichment aggregator that reads pre-computed supply_chain enrichments
from mcp_signal_enrichments table and produces a weighted signal score that
trust_synthesiser can consume.

Bridge between supply_chain_signal_enricher.py (which writes to mcp_signal_enrichments)
and trust_synthesiser_v2 (which reads mcp_signal_scores).
"""

import requests
from typing import Dict, List, Optional, Tuple, Any

# Configuration
WRITE_SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_PORT = 8772
QUERY_ENDPOINT = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}/query"

# Component weights for composite score
COMPONENT_WEIGHTS = {
    "dependency_count_norm": 0.3,
    "publisher_verified": 0.3,
    "registry_reputation": 0.2,
    "release_frequency": 0.2,
}

# Required evidence keys
REQUIRED_EVIDENCE_KEYS = ["verdict", "missing", "component_scores"]


def _query_enrichments(server_name: str, registry_source: Optional[str] = None) -> Dict[str, Any]:
    """
    Query the mcp_signal_enrichments table for supply_chain enrichments.
    
    Args:
        server_name: The name of the MCP server to query.
        registry_source: Optional registry source (e.g., 'npm', 'pypi').
    
    Returns:
        Dict containing the enrichment data or empty dict if not found.
    """
    # Build the SQL query with parameter binding
    sql = """
        SELECT enrichment_data
        FROM mcp_signal_enrichments
        WHERE server_name = %s
        AND signal_type = 'supply_chain_enrichment'
    """
    params = [server_name]
    
    # Add optional registry_source filter
    if registry_source:
        sql += " AND registry_source = %s"
        params.append(registry_source)
    
    sql += " LIMIT 1"
    
    try:
        response = requests.post(
            QUERY_ENDPOINT,
            json={"sql": sql, "params": params},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get("rows") and len(result["rows"]) > 0:
            row = result["rows"][0]
            enrichment_data = row.get("enrichment_data", {})
            if isinstance(enrichment_data, str):
                # Parse JSON string if stored as TEXT
                import json
                enrichment_data = json.loads(enrichment_data)
            return enrichment_data
    except requests.RequestException:
        pass
    except (ValueError, KeyError):
        pass
    
    return {}


def _extract_component_scores(enrichment_data: Dict[str, Any]) -> Dict[str, float]:
    """
    Extract normalized component scores from enrichment data.
    
    Args:
        enrichment_data: Raw enrichment data from the database.
    
    Returns:
        Dict mapping component names to their normalized scores (0-100).
    """
    components = {}
    
    # dependency_count_norm: expected to be 0-100 already, or derived from count
    dep_count = enrichment_data.get("dependency_count", 0)
    dep_count_norm = enrichment_data.get("dependency_count_norm")
    if dep_count_norm is not None:
        components["dependency_count_norm"] = float(dep_count_norm)
    elif dep_count is not None:
        # Normalize: fewer dependencies = higher score (inverse relationship)
        # Max reasonable deps ~100, scale accordingly
        components["dependency_count_norm"] = max(0.0, min(100.0, 100.0 - (float(dep_count) * 2)))
    
    # publisher_verified: boolean or 0/1
    pub_verified = enrichment_data.get("publisher_verified", False)
    if isinstance(pub_verified, bool):
        components["publisher_verified"] = 100.0 if pub_verified else 0.0
    else:
        components["publisher_verified"] = 100.0 if pub_verified else 0.0
    
    # registry_reputation: expected to be 0-100
    reg_rep = enrichment_data.get("registry_reputation")
    if reg_rep is not None:
        components["registry_reputation"] = max(0.0, min(100.0, float(reg_rep)))
    
    # release_frequency: expected to be 0-100 (recency/frequency score)
    rel_freq = enrichment_data.get("release_frequency")
    if rel_freq is not None:
        components["release_frequency"] = max(0.0, min(100.0, float(rel_freq)))
    
    return components


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute weighted composite score from supply chain enrichments.
    
    Args:
        metadata: Dict containing:
            - server_name (required): Name of the MCP server
            - registry_source (optional): Registry source (e.g., 'npm', 'pypi')
    
    Returns:
        Tuple of (composite_score: float in [0,100], evidence: dict)
        
    The evidence dict contains:
        - verdict: Human-readable verdict string
        - missing: List of missing required fields
        - component_scores: Dict of individual component scores
    """
    # Initialize evidence structure
    evidence = {
        "verdict": "unknown",
        "missing": [],
        "component_scores": {}
    }
    
    # Validate required metadata
    server_name = metadata.get("server_name")
    if not server_name:
        evidence["missing"].append("server_name")
        evidence["verdict"] = "invalid_input"
        return 0.0, evidence
    
    registry_source = metadata.get("registry_source")
    
    # Query enrichments from database
    enrichment_data = _query_enrichments(server_name, registry_source)
    
    # Extract component scores
    component_scores = _extract_component_scores(enrichment_data)
    
    # Check for missing components and calculate weighted average
    missing_components = []
    weighted_sum = 0.0
    total_weight = 0.0
    
    for component, weight in COMPONENT_WEIGHTS.items():
        score = component_scores.get(component)
        if score is not None:
            weighted_sum += score * weight
            total_weight += weight
        else:
            missing_components.append(component)
            # Missing components contribute 0
            weighted_sum += 0.0
            total_weight += weight
    
    # Store component scores in evidence
    evidence["component_scores"] = component_scores
    
    # Calculate composite score
    if total_weight > 0:
        composite_score = weighted_sum
    else:
        composite_score = 0.0
    
    # Ensure score is in [0, 100]
    composite_score = max(0.0, min(100.0, composite_score))
    
    # Determine verdict based on score and missing components
    evidence["missing"] = evidence["missing"] + missing_components
    
    if not enrichment_data:
        evidence["verdict"] = "no_enrichment_data"
    elif missing_components:
        evidence["verdict"] = "partial_enrichment"
    elif composite_score >= 75:
        evidence["verdict"] = "highly_trustworthy"
    elif composite_score >= 50:
        evidence["verdict"] = "moderately_trustworthy"
    elif composite_score >= 25:
        evidence["verdict"] = "low_trustworthiness"
    else:
        evidence["verdict"] = "untrustworthy"
    
    return composite_score, evidence


def _run_test_case_1() -> bool:
    """Test case 1: nonexistent server returns score=0, 'missing' in evidence."""
    print("Running test case 1: nonexistent server...")
    score, evidence = compute_score({"server_name": "nonexistent"})
    
    assert isinstance(score, float), f"Expected float, got {type(score)}"
    assert score == 0.0, f"Expected score 0.0, got {score}"
    assert isinstance(evidence, dict), f"Expected dict, got {type(evidence)}"
    assert "missing" in evidence, "Expected 'missing' key in evidence"
    assert "component_scores" in evidence, "Expected 'component_scores' key in evidence"
    
    print(f"  Score: {score}, Evidence: {evidence}")
    print("  PASSED")
    return True


def _run_test_case_2() -> bool:
    """Test case 2: known_mcp with registry_source returns 0<=score<=100."""
    print("Running test case 2: known_mcp with registry_source...")
    score, evidence = compute_score({
        "server_name": "known_mcp",
        "registry_source": "npm"
    })
    
    assert isinstance(score, float), f"Expected float, got {type(score)}"
    assert 0.0 <= score <= 100.0, f"Score {score} out of range [0,100]"
    assert isinstance(evidence, dict), f"Expected dict, got {type(evidence)}"
    
    # Verify all required evidence keys are present
    for key in REQUIRED_EVIDENCE_KEYS:
        assert key in evidence, f"Expected '{key}' in evidence"
    
    print(f"  Score: {score}, Evidence: {evidence}")
    print("  PASSED")
    return True


def _run_test_case_3() -> bool:
    """Test case 3: empty metadata returns score=0, 'server_name' in evidence['missing']."""
    print("Running test case 3: empty metadata...")
    score, evidence = compute_score({})
    
    assert isinstance(score, float), f"Expected float, got {type(score)}"
    assert score == 0.0, f"Expected score 0.0, got {score}"
    assert isinstance(evidence, dict), f"Expected dict, got {type(evidence)}"
    assert "missing" in evidence, "Expected 'missing' key in evidence"
    assert "server_name" in evidence["missing"], "Expected 'server_name' in evidence['missing']"
    
    print(f"  Score: {score}, Evidence: {evidence}")
    print("  PASSED")
    return True


def main():
    """Run all acceptance tests."""
    print("=" * 60)
    print("supply_chain_enrichment_aggregator Acceptance Tests")
    print("=" * 60)
    
    tests_passed = 0
    tests_failed = 0
    
    try:
        if _run_test_case_1():
            tests_passed += 1
    except AssertionError as e:
        print(f"  FAILED: {e}")
        tests_failed += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        tests_failed += 1
    
    try:
        if _run_test_case_2():
            tests_passed += 1
    except AssertionError as e:
        print(f"  FAILED: {e}")
        tests_failed += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        tests_failed += 1
    
    try:
        if _run_test_case_3():
            tests_passed += 1
    except AssertionError as e:
        print(f"  FAILED: {e}")
        tests_failed += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        tests_failed += 1
    
    print("=" * 60)
    print(f"Results: {tests_passed} passed, {tests_failed} failed")
    print("=" * 60)
    
    return tests_failed == 0


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)