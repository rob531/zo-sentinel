# operator_identity_enrichment.py

def compute_score(metadata: dict) -> tuple[float, dict]:
    """Compute a score based on operator identity.

    Args:
        metadata: A dictionary containing operator identity information.

    Returns:
        tuple: A tuple containing the score (float) and evidence (dict).
    """
    score = 0.0
    evidence = {
        'verdict': 'neutral',
        'missing': []
    }

    # Weights for each field
    weights = {
        'operator_identity': 1.0
    }

    # Check if operator_identity is present
    if 'operator_identity' in metadata:
        operator_identity = metadata['operator_identity']
        if operator_identity == 'verified':
            score = 100.0
            evidence['verdict'] = 'verified'
        elif operator_identity == 'unverified':
            score = 0.0
            evidence['verdict'] = 'unverified'
    else:
        evidence['missing'].append('operator_identity')

    return score, evidence

if __name__ == '__main__':
    # Self-test
    score, evidence = compute_score({'operator_identity': 'verified'})
    assert 0 <= score <= 100, "Score out of range"
    assert 'verdict' in evidence, "Verdict missing in evidence"
    print("PASS")
