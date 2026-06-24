def compute_score(metadata: dict) -> (float, dict):
    """
    Computes a weighted enrichment score based on tool_count in metadata.

    Args:
        metadata: Dictionary containing tool_count and other metadata.

    Returns:
        A tuple containing:
            - score: float between 0 and 100
            - evidence: dict with keys 'verdict' and 'missing'
    """
    # Define weights for each field (sum to 1.0)
    weights = {
        'tool_count': 1.0  # Only field considered in this example
    }

    # Initialize score and evidence
    score = 0.0
    evidence = {
        'verdict': '',
        'missing': []
    }

    # Calculate score based on tool_count
    if 'tool_count' in metadata:
        tool_count = metadata['tool_count']
        # Normalize tool_count to a score between 0 and 100
        # Here we assume tool_count is a positive integer and use a simple linear scaling
        # You may need to adjust this based on your specific requirements
        score = min(100.0, tool_count * 10)  # Example scaling factor
        evidence['verdict'] = f"Tool count: {tool_count}"
    else:
        evidence['missing'].append('tool_count')

    return score, evidence

if __name__ == "__main__":
    # Self-test
    score, evidence = compute_score({'tool_count': 5})
    assert 0 <= score <= 100, "Score out of range"
    assert 'verdict' in evidence, "Verdict missing in evidence"
    print("PASS")