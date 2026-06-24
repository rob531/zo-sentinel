"""
Tool Count Diversity Enrichment Module

Computes an enrichment score (0-100) based on tool count and diversity metadata.
Addresses the weak signal from tool_count alone by incorporating multiple metadata fields.

Penalizes extreme values:
- Too few tools: suggests limited scope
- Too many tools: suggests enumeration attack surface

Rewards well-organized tool sets with good schema coverage, naming conventions,
and balanced category distribution.
"""

from typing import Any


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute enrichment score based on tool count diversity metrics.
    
    Combines multiple metadata signals to produce a robust 0-100 score:
    - tool_count: Base count with penalty for extremes
    - tool_name_patterns: Quality of naming conventions
    - tools_with_schema_count: Schema documentation quality
    - tools_without_schema_count: Undocumented tool ratio
    - tool_category_distribution: Category balance metrics
    - dynamic_discovery_indicators: Dynamic tool discovery capabilities
    
    Args:
        metadata: Dictionary containing tool metadata fields
            
    Returns:
        Tuple of (score: float 0-100, evidence: dict with per-field scores)
    """
    evidence = {}
    total_score = 0.0
    
    # 1. Tool Count Score (0-25 points)
    # Penalize extremes - too few = limited scope, too many = enumeration risk
    tool_count = metadata.get('tool_count', 0)
    tool_count_score, tool_count_evidence = _score_tool_count(tool_count)
    evidence['tool_count'] = tool_count_evidence
    total_score += tool_count_score
    
    # 2. Tool Name Patterns Score (0-20 points)
    # Reward organized naming conventions
    tool_name_patterns = metadata.get('tool_name_patterns', [])
    patterns_score, patterns_evidence = _score_tool_name_patterns(tool_name_patterns, tool_count)
    evidence['tool_name_patterns'] = patterns_evidence
    total_score += patterns_score
    
    # 3. Schema Organization Score (0-20 points)
    # Reward high ratio of schema-documented tools
    tools_with_schema = metadata.get('tools_with_schema_count', 0)
    tools_without_schema = metadata.get('tools_without_schema_count', 0)
    schema_score, schema_evidence = _score_schema_organization(
        tools_with_schema, tools_without_schema, tool_count
    )
    evidence['tools_with_schema_count'] = tools_with_schema
    evidence['tools_without_schema_count'] = tools_without_schema
    evidence['schema_organization'] = schema_evidence
    total_score += schema_score
    
    # 4. Category Distribution Score (0-20 points)
    # Reward balanced distribution across categories
    category_distribution = metadata.get('tool_category_distribution', {})
    category_score, category_evidence = _score_category_distribution(
        category_distribution, tool_count
    )
    evidence['tool_category_distribution'] = category_evidence
    total_score += category_score
    
    # 5. Dynamic Discovery Score (0-15 points)
    # Moderate reward for dynamic discovery capabilities
    dynamic_indicators = metadata.get('dynamic_discovery_indicators', [])
    dynamic_score, dynamic_evidence = _score_dynamic_discovery(dynamic_indicators)
    evidence['dynamic_discovery_indicators'] = dynamic_evidence
    total_score += dynamic_score
    
    # Normalize final score to 0-100 range
    final_score = round(min(100.0, max(0.0, total_score)), 2)
    evidence['final_score'] = final_score
    evidence['total_components'] = 5
    
    return final_score, evidence


def _score_tool_count(tool_count: int) -> tuple[float, dict]:
    """
    Score based on tool count, penalizing extreme values.
    
    Ideal range: 10-50 tools indicates balanced scope
    Too few (< 10): limited scope concern
    Too many (> 100): potential enumeration attack surface
    """
    evidence = {
        'raw_value': tool_count,
        'field': 'tool_count',
        'max_points': 25
    }
    
    if tool_count < 3:
        score = 0.0
        reason = 'Critical: Extremely few tools indicates severely limited scope'
    elif tool_count < 5:
        score = 5.0
        reason = 'Warning: Very few tools, limited functionality scope'
    elif tool_count < 10:
        score = 12.0
        reason = 'Caution: Few tools, may have limited operational scope'
    elif tool_count <= 30:
        score = 22.0
        reason = 'Optimal: Good tool count for balanced scope'
    elif tool_count <= 50:
        score = 25.0
        reason = 'Optimal: Excellent tool count range'
    elif tool_count <= 75:
        score = 18.0
        reason = 'Acceptable: Above optimal but within bounds'
    elif tool_count <= 100:
        score = 12.0
        reason = 'Warning: Many tools, potential enumeration attack surface concern'
    else:
        score = 3.0
        reason = 'Critical: Excessive tool count, high enumeration risk'
    
    evidence['partial_score'] = score
    evidence['reason'] = reason
    evidence['assessment'] = 'optimal' if 10 <= tool_count <= 50 else 'penalized'
    
    return score, evidence


def _score_tool_name_patterns(
    tool_name_patterns: Any, 
    tool_count: int
) -> tuple[float, dict]:
    """
    Score based on quality and organization of tool name patterns.
    
    Well-organized naming conventions (prefixes, categories, versioning)
    indicate better tool design and discoverability.
    """
    evidence = {
        'field': 'tool_name_patterns',
        'max_points': 20
    }
    
    if not tool_name_patterns:
        evidence['raw_value'] = []
        evidence['partial_score'] = 0.0
        evidence['reason'] = 'No tool name patterns provided - poor organization'
        return 0.0, evidence
    
    if isinstance(tool_name_patterns, str):
        patterns = [tool_name_patterns]
    elif isinstance(tool_name_patterns, dict):
        patterns = list(tool_name_patterns.keys())
    elif isinstance(tool_name_patterns, (list, tuple, set)):
        patterns = list(tool_name_patterns)
    else:
        patterns = []
    
    evidence['raw_value'] = patterns
    num_patterns = len(patterns)
    evidence['pattern_count'] = num_patterns
    
    # Analyze organization indicators
    has_prefix_conventions = sum(1 for p in patterns if '_' in str(p) or '-' in str(p))
    has_category_separators = sum(1 for p in patterns if '/' in str(p) or '.' in str(p))
    has_versioning = sum(1 for p in patterns if any(c.isdigit() for c in str(p)))
    has_namespacing = sum(1 for p in patterns if '::' in str(p) or '__' in str(p))
    
    # Calculate organization ratio
    org_score = (
        has_prefix_conventions * 0.25 +
        has_category_separators * 0.25 +
        has_versioning * 0.25 +
        has_namespacing * 0.25
    )
    
    # Base score from pattern count relative to tool count
    if tool_count > 0:
        pattern_ratio = num_patterns / tool_count
        if 0.5 <= pattern_ratio <= 1.5:
            base_score = 12.0  # Good coverage
        elif pattern_ratio > 1.5:
            base_score = 10.0  # Over-patterned
        else:
            base_score = 8.0   # Under-documented
    else:
        base_score = 5.0
    
    # Organization bonus (max 8 points)
    org_bonus = min(8.0, org_score * 8)
    total_score = min(20.0, base_score + org_bonus)
    
    evidence['organization_score'] = round(org_score, 2)
    evidence['partial_score'] = round(total_score, 2)
    evidence['reason'] = f'Pattern analysis: {num_patterns} patterns with {round(org_score, 2)} organization score'
    
    return total_score, evidence


def _score_schema_organization(
    tools_with_schema: int,
    tools_without_schema: int,
    tool_count: int
) -> tuple[float, dict]:
    """
    Score based on schema organization of tools.
    
    Higher ratio of tools with schemas indicates better documentation,
    validation, and overall tool quality.
    """
    evidence = {
        'field': 'schema_organization',
        'max_points': 20
    }
    
    total_schema_known = tools_with_schema + tools_without_schema
    
    # Use actual counts if available, otherwise infer from tool_count
    if total_schema_known > 0:
        ratio = tools_with_schema / total_schema_known
    elif tool_count > 0:
        # Assume 50% coverage if not specified
        ratio = 0.5
        evidence['inferred'] = True
    else:
        ratio = 0.0
    
    evidence['raw_value'] = {
        'with_schema': tools_with_schema,
        'without_schema': tools_without_schema
    }
    evidence['schema_ratio'] = round(ratio, 3)
    
    if ratio >= 0.9:
        score = 20.0
        reason = 'Excellent: Nearly all tools have schemas (well-documented)'
    elif ratio >= 0.75:
        score = 17.0
        reason = 'Good: Most tools have schemas'
    elif ratio >= 0.6:
        score = 14.0
        reason = 'Acceptable: Majority of tools have schemas'
    elif ratio >= 0.4:
        score = 10.0
        reason = 'Moderate: Significant portion lacks schemas'
    elif ratio >= 0.2:
        score = 5.0
        reason = 'Poor: Majority of tools lack schemas'
    else:
        score = 0.0
        reason = 'Critical: Almost no tools have schemas (poor organization)'
    
    evidence['partial_score'] = score
    evidence['reason'] = reason
    
    return score, evidence


def _score_category_distribution(
    category_distribution: Any,
    tool_count: int
) -> tuple[float, dict]:
    """
    Score based on tool category distribution.
    
    Rewards:
    - Multiple categories (diversity)
    - Balanced distribution (no single-category dominance)
    - Appropriate category granularity
    
    Penalizes:
    - Single category (monoculture)
    - Extreme skew (one dominant category)
    """
    evidence = {
        'field': 'tool_category_distribution',
        'max_points': 20
    }
    
    if not category_distribution:
        evidence['raw_value'] = {}
        if tool_count > 0:
            evidence['partial_score'] = 5.0
            evidence['reason'] = 'No category distribution - assuming single category (penalized)'
            evidence['num_categories'] = 1
            return 5.0, evidence
        else:
            evidence['partial_score'] = 0.0
            evidence['reason'] = 'No category distribution and no tool count'
            return 0.0, evidence
    
    # Parse distribution
    if isinstance(category_distribution, dict):
        categories = category_distribution
    elif isinstance(category_distribution, (list, tuple)):
        categories = {f'cat_{i}': v for i, v in enumerate(category_distribution)}
    else:
        categories = {}
    
    evidence['raw_value'] = categories
    
    counts = list(categories.values())
    num_categories = len(counts)
    total_in_dist = sum(counts)
    
    evidence['num_categories'] = num_categories
    evidence['total_in_distribution'] = total_in_dist
    
    if num_categories == 0:
        return 0.0, {**evidence, 'partial_score': 0.0, 'reason': 'Empty distribution'}
    
    if num_categories == 1:
        # Single category - check if it's appropriate for tool count
        if tool_count <= 10:
            base_score = 12.0
            reason = 'Single category acceptable for small tool set'
        else:
            base_score = 4.0
            reason = 'Single category for large tool set - poor organization'
        return base_score, {**evidence, 'partial_score': base_score, 'reason': reason}
    
    # Calculate distribution metrics
    avg_per_category = total_in_dist / num_categories
    max_count = max(counts)
    min_count = min(counts) if counts else 0
    
    # Shannon entropy for balance measurement
    import math
    entropy = 0.0
    if total_in_dist > 0:
        for c in counts:
            if c > 0:
                p = c / total_in_dist
                entropy -= p * math.log2(p)
    
    max_entropy = math.log2(num_categories)
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
    
    # Skew ratio: how much the largest category dominates
    skew_ratio = max_count / total_in_dist if total_in_dist > 0 else 1.0
    
    evidence['entropy'] = round(entropy, 3)
    evidence['normalized_entropy'] = round(normalized_entropy, 3)
    evidence['skew_ratio'] = round(skew_ratio, 3)
    
    # Score calculation
    if num_categories >= 5 and normalized_entropy >= 0.8:
        score = 20.0
        reason = 'Excellent: Well-distributed across many categories'
    elif num_categories >= 4 and normalized_entropy >= 0.7:
        score = 17.0
        reason = 'Good: Well-distributed across multiple categories'
    elif num_categories >= 3 and normalized_entropy >= 0.6:
        score = 14.0
        reason = 'Acceptable: Decent distribution'
    elif num_categories >= 3:
        score = 11.0
        reason = 'Moderate: Some imbalance in distribution'
    elif num_categories == 2:
        if skew_ratio < 0.8:
            score = 10.0
            reason = 'Two balanced categories - acceptable'
        else:
            score = 6.0
            reason = 'Two highly unbalanced categories'
    else:
        score = 4.0
        reason = 'Poor distribution - single category dominance'
    
    evidence['partial_score'] = score
    evidence['reason'] = reason
    
    return score, evidence


def _score_dynamic_discovery(dynamic_indicators: Any) -> tuple[float, dict]:
    """
    Score based on dynamic discovery indicators.
    
    Presence of dynamic discovery capabilities indicates sophisticated
    tool management and adaptability.
    """
    evidence = {
        'field': 'dynamic_discovery_indicators',
        'max_points': 15
    }
    
    if not dynamic_indicators:
        evidence['raw_value'] = []
        evidence['partial_score'] = 0.0
        evidence['reason'] = 'No dynamic discovery capabilities indicated'
        return 0.0, evidence
    
    # Parse indicators
    if isinstance(dynamic_indicators, str):
        indicators = [dynamic_indicators]
    elif isinstance(dynamic_indicators, (list, tuple, set)):
        indicators = list(dynamic_indicators)
    elif isinstance(dynamic_indicators, dict):
        indicators = list(dynamic_indicators.keys())
    else:
        indicators = []
    
    evidence['raw_value'] = indicators
    
    # Define indicator weights
    high_value = {
        'auto_discovery', 'runtime_discovery', 'dynamic_loading',
        'plugin_discovery', 'hot_reload', 'runtime_registration'
    }
    medium_value = {
        'lazy_loading', 'plugin_system', 'extension_api',
        'on_demand_loading', 'deferred_init'
    }
    basic_value = {
        'discovery', 'dynamic', 'runtime', 'plugin', 'extension'
    }
    
    weighted_score = 0
    found_types = {'high': 0, 'medium': 0, 'basic': 0}
    
    for indicator in indicators:
        indicator_lower = str(indicator).lower()
        
        if any(h in indicator_lower for h in high_value):
            weighted_score += 3
            found_types['high'] += 1
        elif any(m in indicator_lower for m in medium_value):
            weighted_score += 2
            found_types['medium'] += 1
        elif any(b in indicator_lower for b in basic_value):
            weighted_score += 1
            found_types['basic'] += 1
        else:
            weighted_score += 0.5
            found_types['basic'] += 1
    
    # Normalize to 0-15 range (capped)
    raw_score = min(15.0, weighted_score * 1.5)
    
    evidence['indicator_count'] = len(indicators)
    evidence['weighted_score'] = round(weighted_score, 2)
    evidence['indicator_types'] = found_types
    evidence['partial_score'] = round(raw_score, 2)
    evidence['reason'] = f'Dynamic discovery: {found_types["high"]} high, {found_types["medium"]} medium, {found_types["basic"]} basic indicators'
    
    return raw_score, evidence