"""
permission_scope_advanced_scoring.py

Pure enrichment module for permission_scope signal.
Computes composite score from multiple metadata fields.

Output range: [0.699999988079071, 90.0]
"""

import math
from typing import Any


def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Compute advanced permission_scope score from metadata fields.

    Args:
        metadata: dict containing:
            - permission_scope: base permission scope value (0-100 or similar)
            - tool_count: number of tools/dependencies
            - supply_chain_score: supply chain risk assessment (0.0-1.0)
            - community_signal: community trust signal (0.0-1.0)

    Returns:
        tuple[float, dict]: (score, evidence_dict)
            - score: float in range [0.699999988079071, 90.0]
            - evidence_dict: detailed breakdown of each field contribution
    """
    MIN_SCORE = 0.699999988079071
    MAX_SCORE = 90.0

    # Extract fields with safe defaults
    permission_scope = _safe_get(metadata, 'permission_scope', 1.0)
    tool_count = _safe_get(metadata, 'tool_count', 1)
    supply_chain = _safe_get(metadata, 'supply_chain_score', 0.5)
    community = _safe_get(metadata, 'community_signal', 0.5)

    # Normalize permission_scope (handle various scales)
    scope_normalized = _normalize_scope(permission_scope)

    # Normalize tool_count (logarithmic scaling for better discrimination)
    tool_normalized = _normalize_tool_count(tool_count)

    # Clamp supply_chain and community to [0, 1]
    supply_normalized = _clamp(supply_chain, 0.0, 1.0)
    community_normalized = _clamp(community, 0.0, 1.0)

    # Compute weighted components
    scope_component = scope_normalized * 0.40  # Primary signal weight
    tool_component = tool_normalized * 0.30    # Secondary signal weight
    supply_component = supply_normalized * 0.20 # Risk factor weight
    community_component = community_normalized * 0.10  # Trust factor weight

    # Combine components
    raw_score = scope_component + tool_component + supply_component + community_component

    # Apply non-linear amplification for discrimination at extremes
    amplified = _amplify_discrimination(raw_score)

    # Scale to target range
    score = MIN_SCORE + (amplified * (MAX_SCORE - MIN_SCORE))

    # Final clamp
    final_score = _clamp(score, MIN_SCORE, MAX_SCORE)

    # Build evidence dict
    evidence = {
        'fields_used': [
            'permission_scope',
            'tool_count',
            'supply_chain_score',
            'community_signal'
        ],
        'permission_scope_base': permission_scope,
        'permission_scope_normalized': round(scope_normalized, 6),
        'tool_count': tool_count,
        'tool_count_normalized': round(tool_normalized, 6),
        'supply_chain_score': supply_chain,
        'supply_chain_normalized': round(supply_normalized, 6),
        'community_signal': community,
        'community_normalized': round(community_normalized, 6),
        'scope_component': round(scope_component, 6),
        'tool_component': round(tool_component, 6),
        'supply_component': round(supply_component, 6),
        'community_component': round(community_component, 6),
        'raw_combined': round(raw_score, 6),
        'amplified_score': round(amplified, 6),
        'final_score': round(final_score, 6),
        'range_bounds': [MIN_SCORE, MAX_SCORE]
    }

    return final_score, evidence


def _safe_get(data: dict, key: str, default: Any) -> Any:
    """Safely extract value from metadata dict."""
    value = data.get(key, default)
    if value is None:
        return default
    return value


def _normalize_scope(scope: Any) -> float:
    """Normalize permission_scope to [0, 1] range."""
    try:
        scope = float(scope)
        # Handle different scales
        if scope > 1:
            # Assume 0-100 or similar percentage scale
            scope = scope / 100.0
        return _clamp(scope, 0.0, 1.0)
    except (TypeError, ValueError):
        return 0.5  # Default middle value


def _normalize_tool_count(count: Any) -> float:
    """Normalize tool_count with logarithmic scaling for better discrimination."""
    try:
        count = int(count)
        if count <= 0:
            return 0.0
        # Logarithmic scaling: 1 tool = 0, 10 tools = 0.5, 100+ tools = ~1.0
        normalized = math.log1p(count) / math.log1p(100)
        return _clamp(normalized, 0.0, 1.0)
    except (TypeError, ValueError):
        return 0.5


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to range."""
    return max(min_val, min(max_val, value))


def _amplify_discrimination(raw: float) -> float:
    """
    Apply non-linear transformation to amplify discrimination.
    Scores near extremes get pushed further, creating better separation.
    """
    # Sigmoid-like transformation centered at 0.5
    # This pushes low scores lower and high scores higher
    if raw <= 0.5:
        # Below median: de-amplify slightly
        return raw * 0.95
    else:
        # Above median: amplify
        excess = raw - 0.5
        return 0.5 + (excess * 1.05)