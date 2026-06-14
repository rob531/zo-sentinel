# tool_schema_patterns_enrichment.py
# Pure enrichment module consuming mcp_tool_schema_patterns_v2.py output.
# Scores MCP servers on tool schema efficiency.
# deps: requests

"""
Pure enrichment module scoring MCP servers on tool schema efficiency.

Consumes output from mcp_tool_schema_patterns_v2.py and produces a score
in [0, 100] with evidence dict for the context_efficiency signal.
"""

from __future__ import annotations


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Score an MCP server based on its tool schema patterns.

    Args:
        metadata: dict with keys:
            - tool_count (int): number of tools exposed
            - schema_pattern (str): progressive_disclosure | brute_force_enumeration | hybrid
            - schema_complexity (float, optional): schema complexity score
            - dynamic_discovery (bool, optional): supports dynamic tool discovery

    Returns:
        (score float in [0,100], evidence dict with keys:
            verdict: HIGH_EFFICIENCY | MODERATE | LOW_EFFICIENCY
            pattern: the schema_pattern consumed
            missing: list of missing required keys
        )
    """
    evidence: dict = {
        "verdict": "LOW_EFFICIENCY",
        "pattern": None,
        "missing": [],
    }

    # Check required fields
    if "tool_count" not in metadata:
        evidence["missing"].append("tool_count")
    if "schema_pattern" not in metadata:
        evidence["missing"].append("schema_pattern")

    if evidence["missing"]:
        return 0.0, evidence

    tool_count: int = metadata["tool_count"]
    schema_pattern: str = metadata["schema_pattern"]
    dynamic_discovery: bool = metadata.get("dynamic_discovery", False)

    evidence["pattern"] = schema_pattern

    # Compute base score by pattern
    if schema_pattern == "progressive_disclosure":
        base = 85
        bonus = min(tool_count * 2, 15)
        score = base + bonus
    elif schema_pattern == "brute_force_enumeration":
        base = 40
        bonus = min(tool_count * 0.5, 20)
        score = base + bonus
    elif schema_pattern == "hybrid":
        base = 65
        score = base
    else:
        # Unknown pattern: treat as low efficiency
        evidence["verdict"] = "LOW_EFFICIENCY"
        return 0.0, evidence

    # Dynamic discovery bonus
    if dynamic_discovery:
        score += 10

    # Cap at [0, 100]
    score = max(0.0, min(100.0, score))

    # Set verdict
    if score >= 80:
        evidence["verdict"] = "HIGH_EFFICIENCY"
    elif score >= 50:
        evidence["verdict"] = "MODERATE"
    else:
        evidence["verdict"] = "LOW_EFFICIENCY"

    return score, evidence


if __name__ == "__main__":
    # Acceptance tests
    errors = []

    # Test 1: progressive_disclosure with dynamic_discovery
    score, ev = compute_score({
        "tool_count": 3,
        "schema_pattern": "progressive_disclosure",
        "dynamic_discovery": True,
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    assert "verdict" in ev, "Missing verdict in evidence"
    assert score >= 85, f"Expected score >= 85, got {score}"

    # Test 2: brute_force_enumeration with many tools
    score, ev = compute_score({
        "tool_count": 25,
        "schema_pattern": "brute_force_enumeration",
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    assert "verdict" in ev, "Missing verdict in evidence"
    assert score < 60, f"Expected score < 60, got {score}"

    # Test 3: hybrid
    score, ev = compute_score({
        "tool_count": 8,
        "schema_pattern": "hybrid",
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    assert "verdict" in ev, "Missing verdict in evidence"
    assert 60 < score < 80, f"Expected 60 < score < 80, got {score}"

    # Test 4: empty metadata
    score, ev = compute_score({})
    assert score == 0.0, f"Expected score 0, got {score}"
    assert "tool_count" in ev["missing"], "tool_count should be in missing"
    assert "schema_pattern" in ev["missing"], "schema_pattern should be in missing"

    # Test 5: progressive_disclosure without dynamic_discovery
    score, ev = compute_score({
        "tool_count": 5,
        "schema_pattern": "progressive_disclosure",
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    # base 85 + min(5*2, 15) = 85 + 10 = 95
    assert score == 95.0, f"Expected score 95, got {score}"

    # Test 6: brute_force_enumeration with few tools
    score, ev = compute_score({
        "tool_count": 5,
        "schema_pattern": "brute_force_enumeration",
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    # base 40 + min(5*0.5, 20) = 40 + 2.5 = 42.5
    assert score == 42.5, f"Expected score 42.5, got {score}"

    # Test 7: progressive_disclosure with many tools (cap test)
    score, ev = compute_score({
        "tool_count": 50,
        "schema_pattern": "progressive_disclosure",
        "dynamic_discovery": True,
    })
    assert 0 <= score <= 100, f"Score out of range: {score}"
    # base 85 + min(50*2, 15) + 10 = 85 + 15 + 10 = 110 -> capped to 100
    assert score == 100.0, f"Expected score 100 (capped), got {score}"

    # Test 8: unknown pattern
    score, ev = compute_score({
        "tool_count": 5,
        "schema_pattern": "unknown_pattern",
    })
    assert score == 0.0, f"Expected score 0 for unknown pattern, got {score}"

    print("All tests passed.")