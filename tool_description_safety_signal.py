def compute_score(metadata: dict) -> (float, dict):
    """
    Computes a safety signal score based on the tool description.

    Args:
        metadata: A dictionary containing at least 'tool_description' key.

    Returns:
        A tuple containing:
            - score: A float between 0 and 100 indicating the safety signal.
            - evidence: A dictionary with 'verdict' and 'missing' keys.
    """
    # Define weights for each keyword (sum must be 1.0)
    weights = {
        'safe': 0.4,
        'secure': 0.3,
        'trusted': 0.2,
        'verified': 0.1
    }

    score = 0.0
    missing = []
    evidence = {'verdict': '', 'missing': missing}

    if 'tool_description' not in metadata:
        evidence['verdict'] = 'No tool description provided'
        return (0.0, evidence)

    description = metadata['tool_description'].lower()

    for keyword, weight in weights.items():
        if keyword in description:
            score += weight * 100
        else:
            missing.append(keyword)

    # Normalize score to 0-100 range
    score = min(100, max(0, score))

    if score >= 70:
        evidence['verdict'] = 'High safety signal'
    elif score >= 40:
        evidence['verdict'] = 'Medium safety signal'
    else:
        evidence['verdict'] = 'Low safety signal'

    return (score, evidence)

if __name__ == "__main__":
    # Self-test
    score, evidence = compute_score({'tool_description': 'safe'})
    assert 0 <= score <= 100, f"Score {score} out of range"
    assert 'verdict' in evidence, "Missing verdict in evidence"
    print("PASS")