def compute_score(metadata: dict) -> (float, dict):
    # Define weights for each field (sum to 1.0)
    weights = {
        'registry_source': 0.2,
        'age_days': 0.1,
        'download_count': 0.2,
        'dependency_count': 0.1,
        'publisher_verified': 0.3,
        'stars': 0.1
    }

    # Initialize score and evidence
    score = 0.0
    evidence = {
        'verdict': 'partial',
        'missing': []
    }

    # Calculate score based on available fields
    for field, weight in weights.items():
        if field in metadata:
            value = metadata[field]
            if field == 'registry_source':
                # Normalize registry_source (example: 'npm' -> 1.0, others -> 0.5)
                normalized = 1.0 if value == 'npm' else 0.5
            elif field == 'publisher_verified':
                # Normalize publisher_verified (True -> 1.0, False -> 0.0)
                normalized = 1.0 if value else 0.0
            else:
                # Normalize numerical fields (min-max scaling)
                normalized = min(max(value, 0), 100) / 100  # Clamp between 0 and 100, then scale to 0..1
            score += normalized * weight
        else:
            evidence['missing'].append(field)

    # Scale score to 0..100
    score *= 100

    # Update verdict based on score
    if score >= 80:
        evidence['verdict'] = 'high'
    elif score >= 50:
        evidence['verdict'] = 'medium'
    else:
        evidence['verdict'] = 'low'

    return score, evidence

if __name__ == '__main__':
    # Self-test
    score, evidence = compute_score({'registry_source': 'npm'})
    assert 0 <= score <= 100, f"Score {score} out of range"
    assert 'verdict' in evidence, "Missing verdict in evidence"
    print("PASS")