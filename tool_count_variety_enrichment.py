import re
from typing import Tuple, Dict

def compute_score(metadata: Dict) -> Tuple[float, Dict]:
    """
    Compute enrichment score based on tool count and other metadata fields.

    Args:
        metadata: Dictionary containing tool metadata including:
            - tool_count: int
            - tool_names: list[str]
            - tool_descriptions: list[str]
            - registry_source: str
            - community_metrics: dict

    Returns:
        Tuple containing:
            - score: float between 0-100
            - explanation: dict with scoring details
    """
    # Initialize score and explanation
    score = 0.0
    explanation = {
        'tool_count': metadata.get('tool_count', 0),
        'score_breakdown': {},
        'flags': []
    }

    # Get tool count
    tool_count = metadata.get('tool_count', 0)
    tool_names = metadata.get('tool_names', [])
    tool_descriptions = metadata.get('tool_descriptions', [])
    registry_source = metadata.get('registry_source', '')
    community_metrics = metadata.get('community_metrics', {})

    # Base score based on tool count
    if tool_count <= 5:
        score = 85 - (5 - tool_count) * 3  # 70-85 range
        explanation['score_breakdown']['base'] = f"Low tool count ({tool_count}): {score}"
    elif 6 <= tool_count <= 15:
        score = 70 - (tool_count - 6) * 1  # 55-70 range
        explanation['score_breakdown']['base'] = f"Medium tool count ({tool_count}): {score}"
    elif 16 <= tool_count <= 50:
        score = 50 - (tool_count - 16) * 0.2  # 40-50 range
        explanation['score_breakdown']['base'] = f"High tool count ({tool_count}): {score}"
    else:  # 50+ tools
        score = 40
        explanation['score_breakdown']['base'] = f"Very high tool count ({tool_count}): {score}"

    # Check for progressive disclosure pattern (bonus)
    if tool_count > 50:
        # Simple heuristic: if tool names contain numbers or categories
        pattern_detected = any(
            re.search(r'\d+', name) or
            name.lower() in ['basic', 'advanced', 'expert', 'beginner', 'intermediate']
            for name in tool_names
        )
        if pattern_detected:
            score += 10
            explanation['score_breakdown']['progressive_disclosure'] = "Progressive disclosure pattern detected (+10)"
        else:
            explanation['flags'].append("High tool count without progressive disclosure pattern")

    # Bonus for descriptive tool names
    descriptive_names = sum(1 for name in tool_names if len(name.split()) > 1 and not name.islower())
    if descriptive_names > 0:
        bonus = min(5, descriptive_names * 0.5)
        score += bonus
        explanation['score_breakdown']['descriptive_names'] = f"Descriptive tool names ({descriptive_names}): +{bonus}"

    # Penalize generic enumeration
    generic_names = sum(1 for name in tool_names if re.match(r'Tool \d+', name) or name.lower() in ['tool', 'item', 'option'])
    if generic_names > 0:
        penalty = min(5, generic_names * 0.5)
        score -= penalty
        explanation['score_breakdown']['generic_names'] = f"Generic tool names ({generic_names}): -{penalty}"

    # Community metrics adjustment (if available)
    if community_metrics:
        # Example: adjust based on popularity or reviews
        popularity = community_metrics.get('popularity', 0)
        if popularity > 0.8:
            score += 5
            explanation['score_breakdown']['popularity'] = f"High popularity: +5"
        elif popularity < 0.3:
            score -= 5
            explanation['score_breakdown']['popularity'] = f"Low popularity: -5"

    # Registry source adjustment
    if registry_source.lower() in ['official', 'verified']:
        score += 2
        explanation['score_breakdown']['registry'] = f"Trusted registry: +2"
    elif registry_source.lower() in ['community', 'unverified']:
        score -= 2
        explanation['score_breakdown']['registry'] = f"Unverified registry: -2"

    # Ensure score is within bounds
    score = max(0, min(100, score))

    return round(score, 2), explanation

if __name__ == "__main__":
    # Self-test cases
    test_cases = [
        # Low tool count
        {
            'tool_count': 3,
            'tool_names': ['Search', 'Filter', 'Sort'],
            'tool_descriptions': ['Search items', 'Filter results', 'Sort by date'],
            'registry_source': 'official',
            'community_metrics': {'popularity': 0.9}
        },
        # Medium tool count
        {
            'tool_count': 10,
            'tool_names': ['Basic Tool', 'Advanced Tool', 'Tool 1', 'Tool 2'],
            'tool_descriptions': ['Basic functionality', 'Advanced features'],
            'registry_source': 'community',
            'community_metrics': {'popularity': 0.5}
        },
        # High tool count without pattern
        {
            'tool_count': 30,
            'tool_names': ['Tool A', 'Tool B', 'Tool C'],
            'tool_descriptions': ['Description A', 'Description B'],
            'registry_source': 'verified',
            'community_metrics': {'popularity': 0.7}
        },
        # Very high tool count with pattern
        {
            'tool_count': 60,
            'tool_names': ['Basic 1', 'Basic 2', 'Advanced 1', 'Advanced 2'],
            'tool_descriptions': ['Basic feature 1', 'Basic feature 2'],
            'registry_source': 'official',
            'community_metrics': {'popularity': 0.8}
        }
    ]

    passed = True
    for i, test in enumerate(test_cases):
        score, explanation = compute_score(test)
        print(f"Test case {i+1}:")
        print(f"  Score: {score}")
        print(f"  Explanation: {explanation}")
        print()

        # Verify score is within expected range based on tool_count
        if test['tool_count'] <= 5 and not (70 <= score <= 85):
            passed = False
        elif 6 <= test['tool_count'] <= 15 and not (55 <= score <= 70):
            passed = False
        elif 16 <= test['tool_count'] <= 50 and not (40 <= score <= 60):
            passed = False
        elif test['tool_count'] > 50 and not (40 <= score <= 60):
            passed = False

    print("PASS" if passed else "FAIL")