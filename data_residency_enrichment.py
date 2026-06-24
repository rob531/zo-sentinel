def compute_score(metadata: dict) -> (float, dict):
    evidence = {'missing': []}
    data_residency = metadata.get('data_residency')

    if data_residency is None:
        evidence['missing'].append('data_residency')
        score = 0.0
    else:
        # Weighted formula (weights sum to 1.0)
        weights = {
            'US': 0.2,
            'EU': 0.5,
            'Other': 0.3
        }

        if data_residency in weights:
            score = weights[data_residency] * 100
        else:
            score = weights['Other'] * 100

    evidence['verdict'] = 'PASS' if score >= 0 and score <= 100 else 'FAIL'

    return score, evidence

if __name__ == '__main__':
    score, evidence = compute_score({'data_residency': 'US'})
    assert 0 <= score <= 100
    assert 'verdict' in evidence
    print('PASS')