def compute_score(metadata: dict) -> (float, dict):
    """
    Computes a score for a registry source based on various metadata fields.

    Args:
        metadata: A dictionary containing metadata about the registry source.
                  Expected keys: 'registry_source', 'age_days', 'download_count',
                  'dependency_count', 'publisher_verified'.

    Returns:
        A tuple containing:
            - The computed score (float between 0 and 100).
            - An evidence dictionary with 'verdict' and 'missing' keys.
    """
    weights = {
        'age_days': 0.2,
        'download_count': 0.3,
        'dependency_count': 0.1,
        'publisher_verified': 0.4,
    }
    total_weight = sum(weights.values())
    if total_weight != 1.0:
        raise ValueError("Weights must sum to 1.0")

    score = 0.0
    evidence = {'verdict': '', 'missing': []}

    # Normalize age_days (assuming a reasonable upper bound for scoring)
    max_age_days = 365 * 5  # 5 years
    age_days = metadata.get('age_days')
    if age_days is not None:
        normalized_age = max(0, 1 - (age_days / max_age_days))
        score += normalized_age * weights.get('age_days', 0)
    else:
        evidence['missing'].append('age_days')

    # Normalize download_count (using log scale to handle large numbers)
    download_count = metadata.get('download_count')
    if download_count is not None:
        # Cap downloads at a certain point to avoid extreme scores
        capped_downloads = min(download_count, 1_000_000)
        normalized_downloads = min(1.0, capped_downloads / 1_000_000) if capped_downloads > 0 else 0
        score += normalized_downloads * weights.get('download_count', 0)
    else:
        evidence['missing'].append('download_count')

    # Normalize dependency_count (assuming a reasonable upper bound)
    max_dependency_count = 1000
    dependency_count = metadata.get('dependency_count')
    if dependency_count is not None:
        normalized_dependencies = max(0, 1 - (dependency_count / max_dependency_count))
        score += normalized_dependencies * weights.get('dependency_count', 0)
    else:
        evidence['missing'].append('dependency_count')

    # Publisher verified is a binary factor
    publisher_verified = metadata.get('publisher_verified')
    if publisher_verified is not None:
        score += float(publisher_verified) * weights.get('publisher_verified', 0)
    else:
        evidence['missing'].append('publisher_verified')

    # Scale score to 0-100
    final_score = score * 100

    # Determine verdict based on score
    if final_score >= 75:
        evidence['verdict'] = 'high_confidence'
    elif final_score >= 50:
        evidence['verdict'] = 'medium_confidence'
    elif final_score >= 25:
        evidence['verdict'] = 'low_confidence'
    else:
        evidence['verdict'] = 'very_low_confidence'

    # Add registry_source to evidence if it exists
    if 'registry_source' in metadata:
        evidence['registry_source'] = metadata['registry_source']
    else:
        evidence['missing'].append('registry_source')


    return final_score, evidence

if __name__ == '__main__':
    # Self-test
    test_metadata = {'registry_source': 'npm'}
    score, evidence = compute_score(test_metadata)

    assert 0 <= score <= 100, f"Score out of range: {score}"
    assert 'verdict' in evidence, "'verdict' not found in evidence"
    assert 'missing' in evidence, "'missing' not found in evidence"
    assert 'registry_source' in evidence, "'registry_source' not found in evidence"
    assert 'age_days' in evidence['missing'], "'age_days' should be in missing"
    assert 'download_count' in evidence['missing'], "'download_count' should be in missing"
    assert 'dependency_count' in evidence['missing'], "'dependency_count' should be in missing"
    assert 'publisher_verified' in evidence['missing'], "'publisher_verified' should be in missing"

    print("PASS")

    # More comprehensive test cases
    test_metadata_full = {
        'registry_source': 'pypi',
        'age_days': 730,  # 2 years
        'download_count': 500000,
        'dependency_count': 50,
        'publisher_verified': True
    }
    score_full, evidence_full = compute_score(test_metadata_full)
    print(f"\nTest with full metadata: Score={score_full}, Evidence={evidence_full}")
    assert 0 <= score_full <= 100
    assert 'verdict' in evidence_full
    assert 'missing' in evidence_full and not evidence_full['missing']

    test_metadata_low = {
        'registry_source': 'npm',
        'age_days': 1825, # 5 years
        'download_count': 100,
        'dependency_count': 500,
        'publisher_verified': False
    }
    score_low, evidence_low = compute_score(test_metadata_low)
    print(f"\nTest with low metadata: Score={score_low}, Evidence={evidence_low}")
    assert 0 <= score_low <= 100
    assert 'verdict' in evidence_low
    assert 'missing' in evidence_low and not evidence_low['missing']