def compute_score(metadata: dict) -> (float, dict):
    evidence = {'missing': [], 'verdict': ''}
    score = 0.0

    # Define weights for each trust level
    weights = {
        'high': 0.7,
        'medium': 0.3,
        'low': 0.1
    }

    # Get endpoint_trust from metadata
    endpoint_trust = metadata.get('endpoint_trust')

    if endpoint_trust is None:
        evidence['missing'].append('endpoint_trust')
    else:
        # Calculate score based on weighted formula
        score = weights.get(endpoint_trust, 0.0) * 100
        evidence['verdict'] = f"Endpoint trust is {endpoint_trust}"

    return score, evidence

if __name__ == '__main__':
    score, evidence = compute_score({'endpoint_trust': 'high'})
    assert 0 <= score <= 100, "Score is not within the expected range"
    assert 'verdict' in evidence, "Verdict is missing in evidence"
    print("PASS")