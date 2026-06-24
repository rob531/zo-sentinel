def compute_score(metadata: dict) -> (float, dict):
    # Define weights for each component (sum to 1.0)
    weights = {
        'context_efficiency_score': 0.5,
        'missing_context_count': 0.2,
        'context_usage_frequency': 0.2,
        'context_relevance': 0.1
    }

    # Initialize score and evidence
    score = 0.0
    evidence = {
        'verdict': '',
        'missing': []
    }

    # Calculate weighted score for each component
    for key, weight in weights.items():
        if key in metadata:
            value = metadata[key]
            if key == 'missing_context_count':
                # Invert the count to make it a positive contribution
                value = 1.0 - min(value, 1.0)
            score += value * weight
        else:
            evidence['missing'].append(key)

    # Convert score to 0..100 range
    score *= 100

    # Determine verdict
    if score >= 80:
        evidence['verdict'] = 'Excellent'
    elif score >= 60:
        evidence['verdict'] = 'Good'
    elif score >= 40:
        evidence['verdict'] = 'Fair'
    else:
        evidence['verdict'] = 'Poor'

    return (score, evidence)

if __name__ == '__main__':
    # Self-test
    score, evidence = compute_score({'context_efficiency_score': 0.9})
    assert 0 <= score <= 100, f"Score {score} out of range"
    assert 'verdict' in evidence, "Missing verdict in evidence"
    print("PASS")