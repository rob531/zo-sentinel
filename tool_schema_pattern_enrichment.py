# deps: requests
"""
Tool schema pattern enrichment module.

Scores MCP servers based on their tool-definition architectural patterns.
Progressive-disclosure patterns (few high-level tools with dynamic discovery)
score higher than brute-force enumeration (many tools with full schemas upfront).

Complements supply_chain_enrichment and community_signal_enrichment as the
third canonical enricher per PRODUCT_SPEC §3.

Uses mcp_tool_schema_patterns.py for pattern detection.
"""

from typing import Dict, List, Tuple, Any, Optional
import mcp_tool_schema_patterns


def compute_score(metadata: dict) -> Tuple[float, dict]:
    """
    Compute a score based on tool schema pattern analysis.

    Args:
        metadata: Dict containing:
            - tool_definitions: list of dicts with 'name' and optionally
              'description'/'inputSchema' (optional)
            - registry_source: str (optional)
            - package_name: str (optional)
            - tool_count: int (optional, fallback to len(tool_definitions))

    Returns:
        Tuple of (score in 0..100, evidence dict)

    Evidence keys:
        - verdict: 'progressive_disclosure'|'brute_force'|'hybrid'|'insufficient_data'
        - pattern: str description of detected pattern
        - tool_count: int number of tools
        - high_level_tools: int count of high-level/abstraction tools
        - missing: list of missing required fields
        - signal_type: str = 'tool_schema_pattern'
        - confidence: float = 0.75
    """
    # Initialize evidence with defaults
    evidence: Dict[str, Any] = {
        'verdict': 'insufficient_data',
        'pattern': 'unknown',
        'tool_count': 0,
        'high_level_tools': 0,
        'missing': [],
        'signal_type': 'tool_schema_pattern',
        'confidence': 0.75
    }

    # Extract tool_definitions
    tool_definitions = metadata.get('tool_definitions')
    if tool_definitions is None:
        evidence['missing'].append('tool_definitions')
        return 0.0, evidence

    # Handle non-list tool_definitions
    if not isinstance(tool_definitions, list):
        evidence['missing'].append('tool_definitions')
        evidence['verdict'] = 'insufficient_data'
        evidence['pattern'] = 'invalid_input'
        return 0.0, evidence

    # Determine tool count
    tool_count = metadata.get('tool_count')
    if tool_count is None:
        tool_count = len(tool_definitions)

    evidence['tool_count'] = tool_count

    # Handle empty tool_definitions
    if len(tool_definitions) == 0:
        evidence['missing'].append('tool_definitions')
        evidence['verdict'] = 'insufficient_data'
        evidence['pattern'] = 'empty'
        return 0.0, evidence

    # Classify pattern based on tool count
    if tool_count <= 4:
        score, evidence = _score_progressive_disclosure(
            tool_definitions, tool_count, evidence
        )
    elif tool_count >= 20:
        score, evidence = _score_brute_force(
            tool_definitions, tool_count, evidence
        )
    else:
        score, evidence = _score_hybrid(
            tool_definitions, tool_count, evidence
        )

    return score, evidence


def _score_progressive_disclosure(
    tool_definitions: List[dict],
    tool_count: int,
    evidence: Dict[str, Any]
) -> Tuple[float, Dict[str, Any]]:
    """
    Score progressive-disclosure pattern (tool_count <= 4).
    
    Score range: 85-95 based on schema quality.
    High-level tools expose dynamic discovery mechanisms.
    """
    evidence['verdict'] = 'progressive_disclosure'
    evidence['high_level_tools'] = tool_count

    # Use mcp_tool_schema_patterns if available
    try:
        schema_quality = _assess_schema_quality(tool_definitions)
    except Exception:
        schema_quality = 0.5

    # Score formula: 85-95 based on schema quality (0-1 scale)
    score = 85.0 + (schema_quality * 10.0)

    # Check for discovery indicators
    discovery_score = _assess_discovery_pattern(tool_definitions)
    score = min(95.0, score + discovery_score * 3.0)

    evidence['pattern'] = 'progressive_disclosure'
    return round(score, 1), evidence


def _score_brute_force(
    tool_definitions: List[dict],
    tool_count: int,
    evidence: Dict[str, Any]
) -> Tuple[float, Dict[str, Any]]:
    """
    Score brute-force pattern (tool_count >= 20).
    
    Score range: 20-40 based on duplication and schema repetition.
    Many tools with full schemas upfront indicates enumeration pattern.
    """
    evidence['verdict'] = 'brute_force'

    # Calculate duplication penalty
    duplication = _assess_duplication(tool_definitions)

    # Score formula: 40 down to 20 based on duplication (0-1 scale)
    score = 40.0 - (duplication * 20.0)

    # Identify high-level tools (rough estimate)
    evidence['high_level_tools'] = max(1, tool_count // 15)

    evidence['pattern'] = 'brute_force'
    return max(20.0, round(score, 1)), evidence


def _score_hybrid(
    tool_definitions: List[dict],
    tool_count: int,
    evidence: Dict[str, Any]
) -> Tuple[float, Dict[str, Any]]:
    """
    Score hybrid pattern (4 < tool_count < 20).
    
    Score range: 55-70 based on schema quality and structure.
    Mix of high-level and detailed tools.
    """
    evidence['verdict'] = 'hybrid'

    # Assess schema quality
    schema_quality = _assess_schema_quality(tool_definitions)

    # Score formula: 55-70 based on schema quality
    score = 55.0 + (schema_quality * 15.0)

    # Estimate high-level tools
    evidence['high_level_tools'] = max(1, tool_count // 5)

    evidence['pattern'] = 'hybrid'
    return round(score, 1), evidence


def _assess_schema_quality(tool_definitions: List[dict]) -> float:
    """
    Assess the quality of tool schemas.
    
    Uses mcp_tool_schema_patterns if available.
    
    Returns float in 0..1 representing schema completeness.
    """
    # Try to use mcp_tool_schema_patterns module
    try:
        if hasattr(mcp_tool_schema_patterns, 'analyze_schema_quality'):
            return mcp_tool_schema_patterns.analyze_schema_quality(tool_definitions)
        elif hasattr(mcp_tool_schema_patterns, 'assess_quality'):
            return mcp_tool_schema_patterns.assess_quality(tool_definitions)
        elif hasattr(mcp_tool_schema_patterns, 'compute_quality'):
            return mcp_tool_schema_patterns.compute_quality(tool_definitions)
    except (AttributeError, TypeError):
        pass

    # Fallback: manual quality assessment
    if not tool_definitions:
        return 0.0

    total_quality = 0.0
    for tool in tool_definitions:
        quality = 0.0
        if tool.get('description'):
            desc = tool.get('description', '')
            quality += 0.3
            # Longer descriptions typically indicate better quality
            if len(desc) > 50:
                quality += 0.1
        if tool.get('inputSchema'):
            quality += 0.4
            schema = tool.get('inputSchema', {})
            if isinstance(schema, dict):
                if schema.get('properties'):
                    quality += 0.1
                if schema.get('type'):
                    quality += 0.1
        total_quality += min(1.0, quality)

    return total_quality / len(tool_definitions)


def _assess_duplication(tool_definitions: List[dict]) -> float:
    """
    Assess duplication patterns in tool definitions.
    
    Returns float in 0..1 representing duplication level.
    High duplication suggests brute-force enumeration.
    """
    if not tool_definitions:
        return 0.0

    # Try to use mcp_tool_schema_patterns if available
    try:
        if hasattr(mcp_tool_schema_patterns, 'analyze_duplication'):
            return mcp_tool_schema_patterns.analyze_duplication(tool_definitions)
        elif hasattr(mcp_tool_schema_patterns, 'compute_duplication'):
            return mcp_tool_schema_patterns.compute_duplication(tool_definitions)
    except (AttributeError, TypeError):
        pass

    # Fallback: manual duplication detection
    names = [t.get('name', '') for t in tool_definitions if t.get('name')]

    if not names:
        return 0.5  # Unknown

    unique_names = set(names)
    name_duplication = 1.0 - (len(unique_names) / len(names))

    # Check for schema duplication
    schemas = []
    for tool in tool_definitions:
        schema = tool.get('inputSchema', {})
        if isinstance(schema, dict):
            schemas.append(str(sorted(schema.get('properties', {}).keys())))
        else:
            schemas.append('')

    unique_schemas = len(set(schemas))
    schema_duplication = 1.0 - (unique_schemas / len(schemas)) if len(schemas) > 0 else 0.0

    return (name_duplication * 0.5) + (schema_duplication * 0.5)


def _assess_discovery_pattern(tool_definitions: List[dict]) -> float:
    """
    Assess presence of dynamic discovery mechanisms.
    
    Returns float in 0..1 representing discovery indicator strength.
    """
    discovery_keywords = [
        'discover', 'dynamic', 'enumerate', 'list', 'search',
        'query', 'browse', 'explore', 'find', 'scan'
    ]

    if not tool_definitions:
        return 0.0

    discovery_count = 0
    for tool in tool_definitions:
        name = tool.get('name', '').lower()
        desc = tool.get('description', '').lower()

        if any(kw in name for kw in discovery_keywords):
            discovery_count += 1
        if any(kw in desc for kw in discovery_keywords):
            discovery_count += 0.5

    return min(1.0, discovery_count / len(tool_definitions))


if __name__ == '__main__':
    # Test case 1: Progressive disclosure
    test1 = {'tool_definitions': [{'name': 'cmd', 'description': 'run a command'}]}
    score1, evidence1 = compute_score(test1)
    assert 0 <= score1 <= 100, f"Score {score1} out of range"
    assert 'verdict' in evidence1, "Missing verdict in evidence"
    assert evidence1['verdict'] == 'progressive_disclosure', \
        f"Expected progressive_disclosure, got {evidence1['verdict']}"
    assert score1 > 80, f"Expected score > 80, got {score1}"
    print(f"Test 1 PASS: score={score1}, verdict={evidence1['verdict']}")

    # Test case 2: Brute force enumeration
    test2 = {'tool_definitions': [{'name': f't{i}'} for i in range(20)]}
    score2, evidence2 = compute_score(test2)
    assert 0 <= score2 <= 100, f"Score {score2} out of range"
    assert 'verdict' in evidence2, "Missing verdict in evidence"
    assert evidence2['verdict'] == 'brute_force', \
        f"Expected brute_force, got {evidence2['verdict']}"
    assert score2 < 45, f"Expected score < 45, got {score2}"
    print(f"Test 2 PASS: score={score2}, verdict={evidence2['verdict']}")

    # Test case 3: Insufficient data
    test3 = {}
    score3, evidence3 = compute_score(test3)
    assert score3 == 0, f"Expected score 0, got {score3}"
    assert evidence3['verdict'] == 'insufficient_data', \
        f"Expected insufficient_data, got {evidence3['verdict']}"
    assert 'missing' in evidence3, "Missing 'missing' key in evidence"
    assert 'tool_definitions' in evidence3['missing'], \
        "Expected tool_definitions in missing list"
    print(f"Test 3 PASS: score={score3}, verdict={evidence3['verdict']}")

    print("\nAll tests passed!")